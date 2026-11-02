"""Split a resolution into one main query and linked queries, and compose their SQL."""

from dataclasses import dataclass
from typing import Literal

from psycopg import sql

from app.usecases.graph import SchemaGraph
from app.usecases.resolver import Resolution, Step

Key = tuple[Step, ...]  # fan-out prefix that identifies a query; () is the main query


@dataclass(frozen=True)
class SelectItem:
    table: str
    column: str
    alias: str


@dataclass(frozen=True)
class QuerySpec:
    id: str
    title: str
    kind: Literal["main", "linked"]
    root: str
    joins: tuple[Step, ...]
    select: tuple[SelectItem, ...]
    merge: tuple[tuple[str, str], ...]
    parent_query: str | None


def _split(steps: tuple[Step, ...]) -> tuple[Key, tuple[Step, ...]]:
    """(steps up to and including the last fan-out step, steps after it)."""
    last = max((i for i, s in enumerate(steps) if s.fan_out), default=-1)
    return steps[: last + 1], steps[last + 1 :]


def _ancestors(key: Key) -> list[Key]:
    out: list[Key] = [()]
    fan = [i for i, s in enumerate(key) if s.fan_out]
    out += [key[: i + 1] for i in fan[:-1]]
    return out


def _item(table: str, column: str) -> SelectItem:
    return SelectItem(table, column, f"{table}.{column}")


def plan_queries(graph: SchemaGraph, resolution: Resolution) -> list[QuerySpec]:
    if not resolution.start:
        return []
    ok = [s for s in resolution.fields if s.status == "ok"]
    field_key = {s.field.table: _split(s.path.steps)[0] for s in ok}

    keys: set[Key] = {()}
    for k in field_key.values():
        keys.add(k)
        keys.update(_ancestors(k))
    ordered = sorted(keys, key=lambda k: (len(k), [s.label for s in k]))
    ids = {k: ("main" if not k else f"linked-{i}") for i, k in enumerate(ordered)}

    specs: list[QuerySpec] = []
    for key in ordered:
        children = [c for c in ordered if c and _split(c[:-1])[0] == key]
        needed = {s.field.table for s in ok if field_key[s.field.table] == key}
        needed |= {c[-1].from_table for c in children}
        root = resolution.start if not key else key[-1].to_table
        joins: dict[Step, None] = {}
        for t in sorted(needed, key=lambda t: (resolution.paths[t].cost, t)):
            for step in _split(resolution.paths[t].steps)[1]:
                joins.setdefault(step)

        select: dict[str, SelectItem] = {}
        for s in ok:
            if field_key[s.field.table] == key:
                it = _item(s.field.table, s.field.column)
                select.setdefault(it.alias, it)
        if not key:
            for col in graph.tables[root].pk:
                select.setdefault(_item(root, col).alias, _item(root, col))
        else:
            for col in key[-1].edge.child_cols:
                select.setdefault(_item(root, col).alias, _item(root, col))
        for c in children:
            for col in c[-1].edge.parent_cols:
                it = _item(c[-1].from_table, col)
                select.setdefault(it.alias, it)
        if not select:
            continue  # nothing to show for the main query (start has no key and no fields)

        merge = tuple(zip(key[-1].edge.child_cols, key[-1].edge.parent_cols)) if key else ()
        specs.append(
            QuerySpec(
                id=ids[key],
                title=resolution.start if not key else f"{root} per {key[-1].from_table}",
                kind="linked" if key else "main",
                root=root,
                joins=tuple(joins),
                select=tuple(select.values()),
                merge=merge,
                parent_query=ids[_split(key[:-1])[0]] if key else None,
            )
        )
    return specs


def compose(spec: QuerySpec, graph: SchemaGraph, limit: int) -> sql.Composed:
    """SELECT ... FROM root LEFT JOIN ... LIMIT n. Every name goes through sql.Identifier."""
    alias = {spec.root: sql.Identifier("t0")}
    join_sql: list[sql.Composable] = []
    for i, step in enumerate(spec.joins, start=1):
        new = sql.Identifier(f"t{i}")
        alias[step.to_table] = new
        if step.direction == "up":
            new_cols, old_cols = step.edge.parent_cols, step.edge.child_cols
        else:
            new_cols, old_cols = step.edge.child_cols, step.edge.parent_cols
        cond = sql.SQL(" AND ").join(
            sql.SQL("{}.{} = {}.{}").format(
                new, sql.Identifier(n), alias[step.from_table], sql.Identifier(o)
            )
            for n, o in zip(new_cols, old_cols)
        )
        join_sql.append(
            sql.SQL(" LEFT JOIN {} AS {} ON {}").format(
                sql.Identifier("spp", step.to_table), new, cond
            )
        )
    columns = sql.SQL(", ").join(
        sql.SQL("{}.{} AS {}").format(
            alias[it.table], sql.Identifier(it.column), sql.Identifier(it.alias)
        )
        for it in spec.select
    )
    return sql.SQL("SELECT {} FROM {} AS {}{} LIMIT {}").format(
        columns,
        sql.Identifier("spp", spec.root),
        alias[spec.root],
        sql.SQL("").join(join_sql),
        sql.Literal(limit),
    )
