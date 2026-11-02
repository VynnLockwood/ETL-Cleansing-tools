"""Fixed, deterministic recommendation rules for fields that cannot be reached."""

import re
from collections.abc import Sequence

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from app.usecases.graph import SchemaGraph, Table
from app.usecases.models import Evidence, Fix, Recommendation
from app.usecases.planner import QuerySpec
from app.usecases.preview import read_only_connection
from app.usecases.resolver import Resolution

_MT_COLUMN = re.compile(r"^mt[A-Z]\w*$")
_SEVERITY = {"error": 0, "warning": 1, "info": 2}


def _family(sql_type: str) -> str:
    t = sql_type.lower()
    if t == "uuid":
        return "uuid"
    if t in ("smallint", "integer", "bigint"):
        return "int"
    if t.startswith("numeric") or t in ("real", "double precision"):
        return "num"
    if t == "text" or t.startswith("character"):
        return "text"
    return "other:" + t


def _type_of(table: Table, column: str) -> str:
    return next(c.sql_type for c in table.columns if c.name == column)


def _count(pool: ConnectionPool, timeout_ms: int, holder: str, hcol: str, key: str, kcol: str):
    """(non-null values of holder.hcol, how many of them exist in key.kcol) or None on error."""
    query = sql.SQL(
        "SELECT count(h.{hc}), count(*) FILTER (WHERE h.{hc} IS NOT NULL AND EXISTS"
        " (SELECT 1 FROM {kt} AS k WHERE k.{kc}::text = h.{hc}::text)) FROM {ht} AS h"
    ).format(
        hc=sql.Identifier(hcol),
        kc=sql.Identifier(kcol),
        kt=sql.Identifier("spp", key),
        ht=sql.Identifier("spp", holder),
    )
    try:
        with read_only_connection(pool, timeout_ms) as conn:
            total, matched = conn.execute(query).fetchone()
        return matched, total
    except psycopg.Error:
        return None


def _key_match(pool, graph: SchemaGraph, reached: set[str], unreachable: str, timeout_ms: int):
    """Best foreign-key suggestion that would connect `unreachable` to the reached tables."""
    u = graph.tables[unreachable]
    pairs: list[tuple[str, str, str, str]] = []  # holder, column, key table, key column
    for r in sorted(reached):
        rt = graph.tables[r]
        if len(rt.pk) == 1:
            for c in u.columns:
                if c.name not in u.pk and _family(c.sql_type) == _family(_type_of(rt, rt.pk[0])):
                    pairs.append((unreachable, c.name, r, rt.pk[0]))
        if len(u.pk) == 1:
            for c in rt.columns:
                if c.name not in rt.pk and _family(c.sql_type) == _family(_type_of(u, u.pk[0])):
                    pairs.append((r, c.name, unreachable, u.pk[0]))
    best = None
    for holder, col, key, kcol in pairs:
        counted = _count(pool, timeout_ms, holder, col, key, kcol)
        if counted is None:
            continue
        matched, total = counted
        name_ok = key.lower() in col.lower()
        if not ((total > 0 and matched == total) or (total == 0 and name_ok)):
            continue
        rank = (not name_ok, -matched, holder, col, key, kcol)
        if best is None or rank < best[0]:
            best = (rank, holder, col, key, kcol, matched, total)
    return best


def recommend(
    pool: ConnectionPool,
    graph: SchemaGraph,
    resolution: Resolution,
    queries: Sequence[QuerySpec],
    timeout_ms: int,
) -> list[Recommendation]:
    recs: list[Recommendation] = []
    reached = set(resolution.paths)
    ok_tables = {s.field.table for s in resolution.fields if s.status == "ok"}
    first_unreachable = {}
    for s in resolution.fields:
        if s.status == "unreachable":
            first_unreachable.setdefault(s.field.table, s.field)
    touched = {e.child for e in graph.edges} | {e.parent for e in graph.edges}

    for table, field in sorted(first_unreachable.items()):
        found = _key_match(pool, graph, reached, table, timeout_ms)
        if found:
            _, holder, col, key, kcol, matched, total = found
            msg = f"Add a foreign key {holder}.{col} -> {key}.{kcol}. "
            msg += (
                f"{matched} of {total} values match."
                if total
                else f"No data to confirm: column {holder}.{col} looks like a key to {key} by its name."
            )
            t1 = graph.tables[holder]
            t2 = graph.tables[key]
            a, b = _type_of(t1, col), _type_of(t2, kcol)
            if a != b:
                msg += f" Align the column types first ({a} vs {b})."
            recs.append(
                Recommendation(
                    code="NO_PATH_KEY_MATCH", severity="error", message=msg, table=table,
                    field=field, fix=Fix(table=holder, column=col, ref_table=key, ref_column=kcol),
                    evidence=Evidence(matched=matched, total=total),
                )
            )
        t = graph.tables[table]
        if not t.pk and not t.unique_sets:
            recs.append(
                Recommendation(
                    code="NO_PRIMARY_KEY", severity="error", table=table, field=field,
                    message=f"Table {table} has no primary key or unique key, so nothing can point to it. Define one.",
                )
            )
        if not found and table not in touched:
            recs.append(
                Recommendation(
                    code="ISOLATED_TABLE", severity="error", table=table, field=field,
                    message=(
                        f"Table {table} has no foreign key to or from any table and no column that"
                        " looks like a key to the tables this use case reads. Which column should link it?"
                    ),
                )
            )

    for table in sorted(ok_tables):
        for c in graph.tables[table].columns:
            if _MT_COLUMN.match(c.name) and not c.is_fk and c.name not in graph.tables:
                recs.append(
                    Recommendation(
                        code="MISSING_TABLE", severity="warning", table=table,
                        message=(
                            f"Column {table}.{c.name} looks like a reference to a table '{c.name}'"
                            " which does not exist. Create it."
                        ),
                    )
                )

    for table in sorted(ok_tables):
        p = resolution.paths[table]
        if p.ambiguous:
            used = " ; ".join(s.label for s in p.steps)
            recs.append(
                Recommendation(
                    code="AMBIGUOUS_PATH", severity="warning", table=table,
                    message=(
                        f"{len(p.alternatives) + 1} equal paths reach {table}. Using: {used}."
                        f" Others: {' | '.join(p.alternatives)}."
                    ),
                )
            )

    for q in queries:
        if q.kind == "linked":
            recs.append(
                Recommendation(
                    code="FAN_OUT", severity="info", table=q.root,
                    message=f"'{q.title}': many rows per parent row, shown as a separate query.",
                )
            )

    recs.sort(key=lambda r: (_SEVERITY[r.severity], r.code, r.table or ""))
    return recs
