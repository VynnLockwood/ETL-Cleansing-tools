"""Dictionary mode: build a schema-only Proposal from a data-dictionary workbook."""

import re

from .models import ColumnProfile, ForeignKey, Issue, Proposal, TableProposal, Workbook

_AUDIT_PREFIXES = ("created", "updated", "deleted")
_AUDIT_SUFFIXES = ("Date", "By", "Name", "EmployeeCode", "Position", "Dept")
AUDIT_COLUMNS = frozenset(
    {"isDelete", "version"}
    | {p + s for p in _AUDIT_PREFIXES for s in _AUDIT_SUFFIXES}
)

_FK_RE = re.compile(r"^FK\s*→\s*(\w+)(?:\.(\w+))?\s*$")
_TYPE_RE = re.compile(r"^([a-z][a-z ]*?)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?$")

_SIMPLE_TYPES = {
    "uuid": "uuid",
    "text": "text",
    "boolean": "boolean",
    "bool": "boolean",
    "date": "date",
    "integer": "integer",
    "int": "integer",
    "int4": "integer",
    "smallint": "smallint",
    "bigint": "bigint",
    "double precision": "double precision",
    "real": "real",
    "jsonb": "jsonb",
    "json": "json",
    "timestamp with time zone": "timestamptz",
    "timestamptz": "timestamptz",
    "timestamp without time zone": "timestamp",
    "timestamp": "timestamp",
}


def normalize_type(raw: str) -> str | None:
    """Map a dictionary type string to a Postgres type name; None if unparseable."""
    m = _TYPE_RE.match(" ".join(raw.lower().split()))
    if not m:
        return None
    base, p1, p2 = m.groups()
    if base in ("character varying", "varchar"):
        return f"varchar({p1})" if p1 else "varchar"
    if base in ("numeric", "decimal"):
        if p1 and p2:
            return f"numeric({p1},{p2})"
        return f"numeric({p1})" if p1 else "numeric"
    if p1 is None and base in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[base]
    return None


def _note(cells: list[str]) -> str:
    """The note: an FK cell if any, else the last non-empty cell after 'Data type'."""
    filled = [c.strip() for c in cells if c.strip()]
    for c in filled:
        if _FK_RE.match(c):
            return c
    return filled[-1] if filled else ""


def _is_dictionary_sheet(headers: list[str]) -> bool:
    return (
        len(headers) >= 2
        and headers[0].strip().casefold() == "table"
        and headers[1].strip().casefold() == "column"
    )


def parse_dictionary(wb: Workbook) -> tuple[Proposal, list[Issue]]:
    issues: list[Issue] = []
    # table -> ordered {column: (type, sheet, row)}
    tables: dict[str, dict[str, str]] = {}
    fk_notes: list[tuple[str, str, str, str | None, str, int]] = []

    for sheet in wb.sheets:
        if not _is_dictionary_sheet(sheet.headers):
            continue
        for i, row in enumerate(sheet.rows):
            rownum = i + 2  # header is row 1
            cells = row + [""] * (5 - len(row))
            table, column = cells[0].strip(), cells[1].strip()
            if not table or not column:
                continue
            raw_type = cells[3].strip()
            cols = tables.setdefault(table, {})
            if column in cols:
                issues.append(Issue(
                    sheet=sheet.name, column=column, row=rownum,
                    message=f"duplicate column '{column}' in table '{table}'; ignored"))
                continue
            sql_type = normalize_type(raw_type)
            if sql_type is None:
                issues.append(Issue(
                    sheet=sheet.name, column=column, row=rownum,
                    message=f"unparseable type '{raw_type}'; using text"))
                sql_type = "text"
            cols[column] = sql_type
            if column.endswith("Date") and sql_type in ("boolean", "integer"):
                issues.append(Issue(
                    sheet=sheet.name, column=column, row=rownum,
                    message=f"column '{column}' ends with Date but has type {sql_type}"))
            if re.match(r"^is[A-Z]", column) and sql_type != "boolean":
                issues.append(Issue(
                    sheet=sheet.name, column=column, row=rownum,
                    message=f"column '{column}' starts with 'is' but has type {sql_type}, expected boolean"))
            m = _FK_RE.match(_note(cells[4:]))
            if m:
                fk_notes.append((table, column, m.group(1), m.group(2), sheet.name, rownum))

    # primary keys (decided before audit columns are removed)
    pks: dict[str, list[str]] = {}
    for table, cols in tables.items():
        want = f"{table}id".lower()
        pk = next((c for c in cols if c.lower() == want), None)
        if pk is None and cols:
            first = next(iter(cols))
            if first.endswith("ID") and cols[first] == "uuid":
                pk = first
        pks[table] = [pk] if pk else []

    add_audit = False
    proposals: list[TableProposal] = []
    for table, cols in tables.items():
        kept = []
        for name, sql_type in cols.items():
            if name in AUDIT_COLUMNS:
                add_audit = True
                continue
            kept.append(ColumnProfile(
                name=name, sql_type=sql_type, non_null=0, distinct=0,
                unique=False, samples=[]))
        proposals.append(TableProposal(name=table, columns=kept, primary_key=pks[table]))

    fks: list[ForeignKey] = []
    for table, column, ref_table, ref_column, sheet_name, rownum in fk_notes:
        if column in AUDIT_COLUMNS:
            continue
        accepted = True
        reason = ""
        if ref_table not in tables:
            accepted, reason = False, f"table '{ref_table}' is not in the dictionary"
        elif ref_column is None:
            if pks[ref_table]:
                ref_column = pks[ref_table][0]
            else:
                accepted, reason = False, f"table '{ref_table}' has no primary key"
        elif ref_column not in tables[ref_table]:
            accepted, reason = False, f"column '{ref_table}.{ref_column}' is not in the dictionary"
        if not accepted:
            issues.append(Issue(
                sheet=sheet_name, column=column, row=rownum,
                message=f"foreign key target missing: {reason}"))
        fks.append(ForeignKey(
            table=table, column=column, ref_table=ref_table,
            ref_column=ref_column or "", source="dictionary",
            unmatched=0, accepted=accepted))

    return (
        Proposal(tables=proposals, foreign_keys=fks, add_audit=add_audit, schema_only=True),
        issues,
    )
