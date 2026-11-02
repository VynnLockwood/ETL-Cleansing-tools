"""Generate ordered CREATE TABLE statements from an import Proposal."""

import re

from app.imports.models import ColumnProfile, Proposal

SCHEMA = "spp"
MAX_IDENT_BYTES = 63

_SQL_TYPE_RE = re.compile(
    r"(boolean|smallint|integer|bigint|text|uuid|date|timestamp|timestamptz"
    r"|double precision|jsonb|varchar(\([0-9]+\))?|numeric(\([0-9]+(,[0-9]+)?\))?)"
)

_ACTOR = (
    ("Name", "varchar(255)"),
    ("EmployeeCode", "varchar(50)"),
    ("Position", "varchar(255)"),
    ("Dept", "varchar(512)"),
)

AUDIT_COLUMNS: list[tuple[str, str]] = [
    ("isDelete", "boolean DEFAULT false"),
    ("version", "integer DEFAULT 1"),
    ("createdDate", "timestamptz"),
    ("createdBy", "uuid"),
    *[(f"created{s}", t) for s, t in _ACTOR],
    ("updatedDate", "timestamptz"),
    ("updatedBy", "uuid"),
    *[(f"updated{s}", t) for s, t in _ACTOR],
    ("deletedDate", "timestamptz"),
    ("deletedBy", "varchar(50)"),
    *[(f"deleted{s}", t) for s, t in _ACTOR],
]


class DdlError(ValueError):
    pass


def quote_ident(name: str) -> str:
    if not name:
        raise DdlError("empty identifier")
    if "\x00" in name:
        raise DdlError("identifier contains NUL")
    if len(name.encode("utf-8")) > MAX_IDENT_BYTES:
        raise DdlError(f"identifier longer than {MAX_IDENT_BYTES} bytes: {name[:20]}...")
    return '"' + name.replace('"', '""') + '"'


def _check_type(col: ColumnProfile) -> str:
    if not _SQL_TYPE_RE.fullmatch(col.sql_type):
        raise DdlError(f"unsupported SQL type for column {col.name!r}: {col.sql_type!r}")
    return col.sql_type


def _find_cycle(nodes: list[str], deps: dict[str, set[str]]) -> list[str]:
    """Return a cycle path [a, b, ..., a] among nodes that Kahn could not order."""
    remaining = set(nodes)
    start = next(n for n in nodes if n in remaining)
    path: list[str] = []
    seen: dict[str, int] = {}
    cur = start
    while cur not in seen:
        seen[cur] = len(path)
        path.append(cur)
        cur = next(d for d in sorted(deps[cur]) if d in remaining)
    return path[seen[cur]:] + [cur]


def generate_ddl(p: Proposal) -> tuple[list[str], list[str]]:
    tables = {}
    for t in p.tables:
        if t.name in tables:
            raise DdlError(f"duplicate table: {t.name}")
        names = [c.name for c in t.columns]
        if len(set(names)) != len(names):
            raise DdlError(f"duplicate column in table {t.name}")
        for pk in t.primary_key:
            if pk not in names:
                raise DdlError(f"primary key column {pk!r} not in table {t.name}")
        tables[t.name] = t

    # Accepted foreign keys that resolve inside the proposal.
    fks = []
    extra_unique: dict[str, set[str]] = {}
    for fk in p.foreign_keys:
        if not fk.accepted:
            continue
        src, ref = tables.get(fk.table), tables.get(fk.ref_table)
        if src is None or ref is None:
            continue
        if fk.column not in {c.name for c in src.columns}:
            continue
        ref_cols = {c.name: c for c in ref.columns}
        if fk.ref_column not in ref_cols:
            continue
        if ref.primary_key != [fk.ref_column]:
            if not ref_cols[fk.ref_column].unique:
                raise DdlError(
                    f"foreign key {fk.table}.{fk.column} references "
                    f"{fk.ref_table}.{fk.ref_column}, which is not a primary key or unique column"
                )
            extra_unique.setdefault(fk.ref_table, set()).add(fk.ref_column)
        fks.append(fk)

    # Kahn's algorithm; ties keep input order. Self references do not count.
    deps = {n: {f.ref_table for f in fks if f.table == n and f.ref_table != n} for n in tables}
    names = list(tables)
    done: list[str] = []
    placed: set[str] = set()
    while len(done) < len(names):
        ready = next((n for n in names if n not in placed and deps[n] <= placed), None)
        if ready is None:
            left = [n for n in names if n not in placed]
            raise DdlError("cycle detected: " + " -> ".join(_find_cycle(left, deps)))
        placed.add(ready)
        done.append(ready)

    statements = []
    for name in done:
        t = tables[name]
        lines = []
        for c in t.columns:
            line = f"{quote_ident(c.name)} {_check_type(c)}"
            if c.name in extra_unique.get(name, ()):
                line += " UNIQUE"
            lines.append(line)
        if p.add_audit:
            have = {c.name for c in t.columns}
            lines += [f"{quote_ident(n)} {typ}" for n, typ in AUDIT_COLUMNS if n not in have]
        if t.primary_key:
            lines.append("PRIMARY KEY (" + ", ".join(quote_ident(c) for c in t.primary_key) + ")")
        for f in fks:
            if f.table == name:
                lines.append(
                    f"FOREIGN KEY ({quote_ident(f.column)}) REFERENCES "
                    f"{quote_ident(SCHEMA)}.{quote_ident(f.ref_table)} ({quote_ident(f.ref_column)})"
                )
        body = ",\n  ".join(lines)
        statements.append(f"CREATE TABLE {quote_ident(SCHEMA)}.{quote_ident(name)} (\n  {body}\n)")
    return statements, done
