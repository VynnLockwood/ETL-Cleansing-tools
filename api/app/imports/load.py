"""Load workbook sheets into tables that already exist in schema spp."""

import re

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from app.imports.apply import convert_value
from app.imports.ddl import SCHEMA
from app.imports.models import ApplyResult, Issue, Sheet, Workbook

_COLUMNS_SQL = """
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attgenerated <> ''
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

_FKS_SQL = """
SELECT t.relname, r.relname
FROM pg_catalog.pg_constraint k
JOIN pg_catalog.pg_class t ON t.oid = k.conrelid
JOIN pg_catalog.pg_class r ON r.oid = k.confrelid
JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
JOIN pg_catalog.pg_namespace rn ON rn.oid = r.relnamespace
WHERE k.contype = 'f' AND n.nspname = %s AND rn.nspname = %s
"""

_SIMPLE = {"integer", "bigint", "smallint", "boolean", "date", "uuid", "text", "double precision"}
_PARAM = re.compile(r"(varchar|numeric)\([0-9]+(,[0-9]+)?\)")


def _normalize_type(pg_type: str) -> str | None:
    """Map format_type() output to a type convert_value understands, else None."""
    t = pg_type.replace("character varying", "varchar")
    t = t.replace("timestamp with time zone", "timestamptz")
    t = t.replace("timestamp without time zone", "timestamp")
    if t in _SIMPLE or t in ("timestamptz", "timestamp") or _PARAM.fullmatch(t):
        return t
    return None


def _db_message(e: psycopg.Error) -> str:
    primary = e.diag.message_primary
    if not primary:
        return str(e).strip() or type(e).__name__
    detail = e.diag.message_detail
    return f"{primary} ({detail})" if detail else primary


def _order(names: list[str], edges: set[tuple[str, str]]) -> list[str]:
    """Parents before children; ties and cycles keep sheet order."""
    deps = {n: {p for c, p in edges if c == n and p != n and p in names} for n in names}
    done: list[str] = []
    while len(done) < len(names):
        ready = next((n for n in names if n not in done and deps[n] <= set(done)), None)
        if ready is None:  # cycle: let the database enforce the rest
            ready = next(n for n in names if n not in done)
        done.append(ready)
    return done


def _convert(sheet: Sheet, cols: dict[str, tuple[str, bool]], issues: list[Issue]):
    """Return (column names, converted rows) for a sheet, appending issues."""
    ok = True
    seen: set[str] = set()
    types: list[str] = []
    for h in sheet.headers:
        if h in seen:
            issues.append(Issue(sheet=sheet.name, column=h, row=1, message="duplicate header"))
            ok = False
        elif h not in cols:
            issues.append(Issue(sheet=sheet.name, column=h, row=1,
                                message=f"header is not a column of table '{sheet.name}'"))
            ok = False
        else:
            pg_type, generated = cols[h]
            norm = _normalize_type(pg_type)
            if generated:
                issues.append(Issue(sheet=sheet.name, column=h, row=1,
                                    message="column is generated and cannot be loaded"))
                ok = False
            elif norm is None:
                issues.append(Issue(sheet=sheet.name, column=h, row=1,
                                    message=f"column type {pg_type} is not supported for loading"))
                ok = False
            else:
                types.append(norm)
        seen.add(h)
    if not ok:
        return sheet.headers, []
    out: list[list[object | None]] = []
    for n, row in enumerate(sheet.rows, start=2):
        values: list[object | None] = []
        for i, h in enumerate(sheet.headers):
            text = row[i] if i < len(row) else ""
            try:
                values.append(convert_value(types[i], text))
            except ValueError as e:
                issues.append(Issue(sheet=sheet.name, column=h, row=n, message=str(e)))
                values.append(None)
        out.append(values)
    return sheet.headers, out


def load_existing(pool: ConnectionPool, wb: Workbook) -> ApplyResult:
    sheets: dict[str, Sheet] = {}
    for s in wb.sheets:
        sheets.setdefault(s.name, s)

    issues: list[Issue] = []
    current = ""
    try:
        with pool.connection() as conn, conn.transaction():
            tables: dict[str, dict[str, tuple[str, bool]]] = {}
            for rel, col, typ, generated in conn.execute(_COLUMNS_SQL, (SCHEMA,)):
                tables.setdefault(rel, {})[col] = (typ, generated)
            edges = {(c, p) for c, p in conn.execute(_FKS_SQL, (SCHEMA, SCHEMA))}

            known = [n for n in sheets if n in tables]
            for n in sheets:
                if n not in tables:
                    issues.append(Issue(sheet=n, column="", row=0,
                                        message=f"no table named '{n}' in schema {SCHEMA}"))
            loads = []
            for name in _order(known, edges):
                headers, rows = _convert(sheets[name], tables[name], issues)
                loads.append((name, headers, rows))
            if issues:
                return ApplyResult(created=[], skipped=[], issues=issues)

            rows_loaded: dict[str, int] = {}
            for name, headers, rows in loads:
                current = name
                if rows and headers:
                    cols = sql.SQL(", ").join(sql.Identifier(h) for h in headers)
                    query = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
                        sql.Identifier(SCHEMA), sql.Identifier(name), cols
                    )
                    with conn.cursor() as cur, cur.copy(query) as copy:
                        for row in rows:
                            copy.write_row(row)
                rows_loaded[name] = len(rows)
    except psycopg.Error as e:
        return ApplyResult(created=[], skipped=[], issues=[
            Issue(sheet=current, column="", row=0, message=_db_message(e))])
    return ApplyResult(created=[], skipped=[], issues=[], rows_loaded=rows_loaded)
