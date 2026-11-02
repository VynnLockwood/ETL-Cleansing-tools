"""Foreign-key graph of schema `spp`, read from the Postgres catalog."""

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    is_pk: bool
    is_fk: bool


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    pk: tuple[str, ...]
    unique_sets: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Edge:
    name: str
    child: str
    parent: str
    child_cols: tuple[str, ...]
    parent_cols: tuple[str, ...]
    one_to_one: bool


@dataclass(frozen=True)
class SchemaGraph:
    tables: dict[str, Table]
    edges: tuple[Edge, ...]


_COLUMNS_SQL = """
SELECT c.oid, c.relname, a.attnum, a.attname, format_type(a.atttypid, a.atttypmod)
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
WHERE c.relnamespace = 'spp'::regnamespace
  AND c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

_CONSTRAINTS_SQL = """
SELECT conrelid, contype, conname, conkey, confrelid, confkey
FROM pg_catalog.pg_constraint
WHERE connamespace = 'spp'::regnamespace AND contype IN ('p', 'u', 'f')
"""

_UNIQUE_INDEXES_SQL = """
SELECT indrelid, indkey::int2[]
FROM pg_catalog.pg_index
WHERE indisunique AND NOT indisprimary AND indexprs IS NULL AND indpred IS NULL
  AND indrelid IN (SELECT oid FROM pg_catalog.pg_class
                   WHERE relnamespace = 'spp'::regnamespace)
"""


def load_graph(conn: psycopg.Connection) -> SchemaGraph:
    names: dict[int, str] = {}
    attnames: dict[int, dict[int, str]] = {}
    types: dict[int, dict[int, str]] = {}
    order: dict[int, list[int]] = {}
    for oid, relname, attnum, attname, typ in conn.execute(_COLUMNS_SQL):
        names[oid] = relname
        attnames.setdefault(oid, {})[attnum] = attname
        types.setdefault(oid, {})[attnum] = typ
        order.setdefault(oid, []).append(attnum)

    def cols(oid: int, nums: list[int]) -> tuple[str, ...]:
        return tuple(attnames[oid][n] for n in nums)

    pk: dict[int, tuple[str, ...]] = {}
    unique: dict[int, list[tuple[str, ...]]] = {}
    fks: list[tuple[str, int, tuple[str, ...], int, tuple[str, ...]]] = []
    for relid, ctype, cname, conkey, frelid, fkey in conn.execute(_CONSTRAINTS_SQL):
        if relid not in names:
            continue
        if ctype == "p":
            pk[relid] = cols(relid, conkey)
        elif ctype == "u":
            unique.setdefault(relid, []).append(cols(relid, conkey))
        elif ctype == "f" and frelid in names:
            fks.append((cname, relid, cols(relid, conkey), frelid, cols(frelid, fkey)))
    for relid, indkey in conn.execute(_UNIQUE_INDEXES_SQL):
        if relid in names and all(n > 0 for n in indkey):
            sets = unique.setdefault(relid, [])
            t = cols(relid, list(indkey))
            if t not in sets:
                sets.append(t)

    fk_cols: dict[int, set[str]] = {}
    for _, relid, child_cols, _, _ in fks:
        fk_cols.setdefault(relid, set()).update(child_cols)

    tables: dict[str, Table] = {}
    for oid in sorted(names, key=lambda o: names[o]):
        pk_cols = pk.get(oid, ())
        columns = tuple(
            Column(
                name=attnames[oid][n],
                sql_type=types[oid][n],
                is_pk=attnames[oid][n] in pk_cols,
                is_fk=attnames[oid][n] in fk_cols.get(oid, set()),
            )
            for n in order[oid]
        )
        tables[names[oid]] = Table(
            name=names[oid],
            columns=columns,
            pk=pk_cols,
            unique_sets=tuple(sorted(unique.get(oid, []))),
        )

    edges = []
    for cname, relid, child_cols, frelid, parent_cols in fks:
        child = tables[names[relid]]
        keys = [frozenset(child.pk)] if child.pk else []
        keys += [frozenset(u) for u in child.unique_sets]
        edges.append(
            Edge(
                name=cname,
                child=names[relid],
                parent=names[frelid],
                child_cols=child_cols,
                parent_cols=parent_cols,
                one_to_one=frozenset(child_cols) in keys,
            )
        )
    edges.sort(key=lambda e: (e.child, e.parent, e.name))
    return SchemaGraph(tables=tables, edges=tuple(edges))
