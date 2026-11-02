"""Fixed, deterministic recommendation rules for fields that cannot be reached."""

import re
import time
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


def _count(
    pool: ConnectionPool, timeout_ms: int, holder: str, hcol: str, key: str, kcol: str, same_type: bool
):
    """(matched, total) for holder.hcol against key.kcol, or None when the query fails or times out."""
    left = sql.SQL("k.{kc}").format(kc=sql.Identifier(kcol))
    right = sql.SQL("h.{hc}").format(hc=sql.Identifier(hcol))
    cond = (
        sql.SQL("{} = {}").format(left, right)
        if same_type
        else sql.SQL("{}::text = {}::text").format(left, right)
    )
    query = sql.SQL(
        "SELECT count(h.{hc}), count(*) FILTER (WHERE h.{hc} IS NOT NULL AND EXISTS"
        " (SELECT 1 FROM {kt} AS k WHERE {cond})) FROM {ht} AS h"
    ).format(
        hc=sql.Identifier(hcol),
        cond=cond,
        kt=sql.Identifier("spp", key),
        ht=sql.Identifier("spp", holder),
    )
    try:
        with read_only_connection(pool, timeout_ms) as conn:
            total, matched = conn.execute(query).fetchone()
        return matched, total
    except psycopg.Error:
        return None


def _key_match(
    pool, graph: SchemaGraph, used: set[str], unreachable: str, timeout_ms: int, deadline: float
):
    """Best foreign-key suggestion that would connect `unreachable` to the tables the use case uses.

    Returns (best or None, incomplete). `incomplete` is true when some candidate could not be
    checked (timeout, error, or the time budget ran out)."""
    u = graph.tables[unreachable]
    pairs: list[tuple[str, str, str, str]] = []  # holder, column, key table, key column
    for r in sorted(used):
        rt = graph.tables[r]
        if len(rt.pk) == 1:
            for c in u.columns:
                if c.name not in u.pk and not c.is_fk and _family(c.sql_type) == _family(
                    _type_of(rt, rt.pk[0])
                ):
                    pairs.append((unreachable, c.name, r, rt.pk[0]))
        if len(u.pk) == 1:
            for c in rt.columns:
                if c.name not in rt.pk and not c.is_fk and _family(c.sql_type) == _family(
                    _type_of(u, u.pk[0])
                ):
                    pairs.append((r, c.name, unreachable, u.pk[0]))
    best = None
    incomplete = False
    for holder, col, key, kcol in pairs:
        if time.monotonic() > deadline:
            incomplete = True
            break
        same = _type_of(graph.tables[holder], col) == _type_of(graph.tables[key], kcol)
        counted = _count(pool, timeout_ms, holder, col, key, kcol, same)
        if counted is None:
            incomplete = True
            continue
        matched, total = counted
        name_ok = key.lower() in col.lower()
        if not ((total > 0 and matched == total) or (total == 0 and name_ok)):
            continue
        rank = (not name_ok, -matched, holder, col, key, kcol)
        if best is None or rank < best[0]:
            best = (rank, holder, col, key, kcol, matched, total)
    return best, incomplete


def recommend(
    pool: ConnectionPool,
    graph: SchemaGraph,
    resolution: Resolution,
    queries: Sequence[QuerySpec],
    timeout_ms: int,
) -> list[Recommendation]:
    recs: list[Recommendation] = []
    ok_tables = {s.field.table for s in resolution.fields if s.status == "ok"}
    # Tables the use case actually uses: start, field tables and the tables on their paths.
    used = {resolution.start} | ok_tables
    for s in resolution.fields:
        if s.status == "ok":
            used |= {t for step in s.path.steps for t in (step.from_table, step.to_table)}
    used &= set(graph.tables)
    deadline = time.monotonic() + 4 * timeout_ms / 1000
    first_unreachable = {}
    for s in resolution.fields:
        if s.status == "unreachable":
            first_unreachable.setdefault(s.field.table, s.field)
    touched = {e.child for e in graph.edges} | {e.parent for e in graph.edges}

    for table, field in sorted(first_unreachable.items()):
        found, incomplete = _key_match(pool, graph, used, table, timeout_ms, deadline)
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
            if incomplete:
                msg += " Not every column could be checked (time limit), so a better key may exist."
            recs.append(
                Recommendation(
                    code="NO_PATH_KEY_MATCH", severity="error", message=msg, table=table,
                    field=field, fix=Fix(table=holder, column=col, ref_table=key, ref_column=kcol),
                    evidence=Evidence(matched=matched, total=total),
                )
            )
        elif incomplete:
            recs.append(
                Recommendation(
                    code="NO_PATH_KEY_MATCH", severity="warning", table=table, field=field,
                    message=(
                        f"Could not check every column of the tables around {table} for a matching key"
                        " (time limit); no key was confirmed. Try again, or check the data by hand."
                    ),
                )
            )
        t = graph.tables[table]
        holds_key = bool(found) and found[1] == table
        if not t.pk and not t.unique_sets and not holds_key:
            recs.append(
                Recommendation(
                    code="NO_PRIMARY_KEY", severity="error", table=table, field=field,
                    message=f"Table {table} has no primary key or unique key, so nothing can point to it. Define one.",
                )
            )
        if not found and not incomplete and table not in touched:
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
            if (
                _MT_COLUMN.match(c.name)
                and not c.is_fk
                and not c.is_pk
                and not any(c.name.startswith(t) for t in graph.tables if _MT_COLUMN.match(t))
            ):
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
