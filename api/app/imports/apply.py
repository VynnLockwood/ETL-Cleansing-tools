"""Apply an import Proposal to the database in a single transaction."""

import math
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from psycopg import sql
from psycopg_pool import ConnectionPool

from app.imports.ddl import SCHEMA, generate_ddl
from app.imports.models import ApplyResult, Issue, Proposal, Sheet, Workbook

_INT_RE = re.compile(r"[+-]?[0-9]+", re.ASCII)
_DEC_RE = re.compile(r"[+-]?[0-9]+(\.[0-9]+)?", re.ASCII)
_FLOAT_RE = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?", re.ASCII)
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}", re.ASCII)
_NUMERIC_RE = re.compile(r"numeric\(([0-9]+)(?:,([0-9]+))?\)")
_VARCHAR_RE = re.compile(r"varchar\(([0-9]+)\)")

_INT_RANGES = {
    "smallint": 2**15,
    "integer": 2**31,
    "bigint": 2**63,
}


class _Rollback(Exception):
    """Unwinds the import transaction after conversion issues were collected."""


def convert_value(sql_type: str, text: str) -> object | None:
    """Convert one cell to a Python value for ``sql_type``; blank -> None.

    Raises ValueError with a short message if the text does not fit the type.
    """
    if text.strip() == "":
        return None
    if sql_type == "boolean":
        low = text.strip().lower()
        if low in ("true", "false"):
            return low == "true"
        raise ValueError("expected true or false")
    if sql_type in _INT_RANGES:
        s = text.strip()
        if not _INT_RE.fullmatch(s):
            raise ValueError("expected a whole number")
        n = int(s)
        limit = _INT_RANGES[sql_type]
        if not -limit <= n < limit:
            raise ValueError(f"number out of range for {sql_type}")
        return n
    m = _NUMERIC_RE.fullmatch(sql_type)
    if m:
        precision, scale = int(m.group(1)), int(m.group(2) or 0)
        s = text.strip()
        if not _DEC_RE.fullmatch(s):
            raise ValueError("expected a number")
        d = Decimal(s)
        # Inspect digits/exponent: quantize() would hit the 28-digit context limit.
        _, digits, exp = d.as_tuple()
        digits = list(digits)
        while exp < 0 and len(digits) > 1 and digits[-1] == 0:
            digits.pop()
            exp += 1
        if d == 0:
            frac_digits, int_digits = 0, 0
        else:
            frac_digits = max(-exp, 0)
            int_digits = max(len(digits) + exp, 0)
        if frac_digits > scale:
            raise ValueError(f"more than {scale} decimal places")
        if int_digits > precision - scale:
            raise ValueError(f"too many digits for {sql_type}")
        return d
    if sql_type == "double precision":
        s = text.strip()
        if not _FLOAT_RE.fullmatch(s):
            raise ValueError("expected a number")
        f = float(s)
        if not math.isfinite(f):
            raise ValueError("number out of range")
        return f
    if sql_type == "date":
        s = text.strip()
        if not _DATE_RE.fullmatch(s):
            raise ValueError("expected a date as YYYY-MM-DD")
        try:
            return date.fromisoformat(s)
        except ValueError:
            raise ValueError("not a valid date") from None
    if sql_type in ("timestamp", "timestamptz"):
        s = text.strip()
        if _DATE_RE.fullmatch(s):  # Excel midnight values are rendered date-only
            s += "T00:00:00"
        if not _TS_RE.fullmatch(s):
            raise ValueError("expected a timestamp as YYYY-MM-DDTHH:MM:SS")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            raise ValueError("not a valid timestamp") from None
    if sql_type == "uuid":
        try:
            return uuid.UUID(text.strip())
        except ValueError:
            raise ValueError("not a valid UUID") from None
    if "\x00" in text:
        raise ValueError("text contains a NUL character")
    if sql_type == "text":
        return text
    m = _VARCHAR_RE.fullmatch(sql_type)
    if m:
        if len(text) > int(m.group(1)):
            raise ValueError(f"longer than {m.group(1)} characters")
        return text
    raise ValueError(f"unsupported type {sql_type}")


def _convert_table(t, sheet: Sheet, issues: list[Issue]) -> list[list[object | None]]:
    wanted = [c.name for c in t.columns]
    seen: set[str] = set()
    ok = True
    for h in sheet.headers:
        if h in seen:
            issues.append(Issue(sheet=sheet.name, column=h, row=1, message="duplicate header"))
            ok = False
        elif h not in wanted:
            issues.append(Issue(sheet=sheet.name, column=h, row=1,
                                message="header is not a column of the table"))
            ok = False
        seen.add(h)
    for name in wanted:
        if name not in seen:
            issues.append(Issue(sheet=sheet.name, column=name, row=1,
                                message="column is missing from the sheet"))
            ok = False
    if not ok:
        return []
    index = {h: i for i, h in enumerate(sheet.headers)}
    out: list[list[object | None]] = []
    for n, row in enumerate(sheet.rows, start=2):
        values: list[object | None] = []
        for c in t.columns:
            i = index[c.name]
            text = row[i] if i < len(row) else ""
            try:
                values.append(convert_value(c.sql_type, text))
            except ValueError as e:
                issues.append(Issue(sheet=sheet.name, column=c.name, row=n, message=str(e)))
                values.append(None)
        out.append(values)
    return out


def apply(pool: ConnectionPool, p: Proposal, wb: Workbook) -> ApplyResult:
    statements, order = generate_ddl(p)
    by_name = {t.name: t for t in p.tables}
    sheets: dict[str, Sheet] = {}
    for s in wb.sheets:
        sheets.setdefault(s.name, s)

    issues: list[Issue] = []
    skipped: list[str] = []
    created: list[str] = []
    rows_loaded: dict[str, int] = {}
    try:
        with pool.connection() as conn, conn.transaction():
            existing = {
                r[0]
                for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                    (SCHEMA,),
                )
            }
            skipped = [n for n in order if n in existing]
            for stmt, name in zip(statements, order):
                if name in existing:
                    continue
                conn.execute(stmt)
                conn.execute(
                    sql.SQL("GRANT SELECT ON {}.{} TO spp_reader").format(
                        sql.Identifier(SCHEMA), sql.Identifier(name)
                    )
                )
                created.append(name)

            loads: list[tuple[str, list[list[object | None]]]] = []
            if not p.schema_only:
                for name in created:
                    sheet = sheets.get(name)
                    rows = _convert_table(by_name[name], sheet, issues) if sheet else []
                    loads.append((name, rows))
            if issues:
                raise _Rollback

            for name, rows in loads:
                if rows:
                    cols = sql.SQL(", ").join(sql.Identifier(c.name) for c in by_name[name].columns)
                    query = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
                        sql.Identifier(SCHEMA), sql.Identifier(name), cols
                    )
                    with conn.cursor() as cur, cur.copy(query) as copy:
                        for row in rows:
                            copy.write_row(row)
                rows_loaded[name] = len(rows)
    except _Rollback:
        return ApplyResult(created=[], skipped=skipped, issues=issues)
    return ApplyResult(created=created, skipped=skipped, issues=[], rows_loaded=rows_loaded)
