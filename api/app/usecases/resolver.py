"""Start table choice and shortest join paths over the foreign-key graph (pure, no database)."""

import heapq
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.usecases.graph import Edge, SchemaGraph
from app.usecases.models import FieldRef

_MAX_ALTERNATIVES = 3


@dataclass(frozen=True)
class Step:
    edge: Edge
    direction: Literal["up", "down"]

    @property
    def cost(self) -> int:
        if self.direction == "up" or self.edge.one_to_one:
            return 1
        return 3

    @property
    def fan_out(self) -> bool:
        return self.direction == "down" and not self.edge.one_to_one

    @property
    def from_table(self) -> str:
        return self.edge.child if self.direction == "up" else self.edge.parent

    @property
    def to_table(self) -> str:
        return self.edge.parent if self.direction == "up" else self.edge.child

    @property
    def label(self) -> str:
        return f"{self.edge.child}.{','.join(self.edge.child_cols)} -> {self.edge.parent}"


@dataclass(frozen=True)
class Path:
    table: str
    steps: tuple[Step, ...]
    cost: int
    ambiguous: bool
    alternatives: tuple[str, ...]


@dataclass(frozen=True)
class FieldStatus:
    field: FieldRef
    status: Literal["ok", "unreachable", "missing"]
    path: Path | None


@dataclass(frozen=True)
class Resolution:
    start: str
    start_reason: str
    paths: dict[str, Path]
    fields: tuple[FieldStatus, ...]


def _adjacency(graph: SchemaGraph) -> dict[str, list[Step]]:
    adj: dict[str, list[Step]] = {}
    for e in graph.edges:
        if e.child == e.parent:
            continue  # a self-reference never reaches a new table
        adj.setdefault(e.child, []).append(Step(e, "up"))
        adj.setdefault(e.parent, []).append(Step(e, "down"))
    return adj


def _paths_from(start: str, adj: dict[str, list[Step]]) -> dict[str, Path]:
    dist = {start: 0}
    heap = [(0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        for step in adj.get(u, ()):
            v = step.to_table
            nd = d + step.cost
            if nd < dist.get(v, 1 << 60):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))

    # every shortest-path predecessor of each node, ordered by edge name for determinism
    preds: dict[str, list[Step]] = {v: [] for v in dist}
    for u in dist:
        for step in adj.get(u, ()):
            v = step.to_table
            if dist[u] + step.cost == dist[v]:
                preds[v].append(step)
    for v in preds:
        preds[v].sort(key=lambda s: (s.edge.name, s.from_table, s.direction))

    ways = {start: 1}
    for v in sorted(dist, key=lambda t: (dist[t], t)):
        if v != start:
            ways[v] = sum(ways[s.from_table] for s in preds[v])

    def enumerate_paths(v: str, limit: int) -> list[tuple[Step, ...]]:
        if v == start:
            return [()]
        out: list[tuple[Step, ...]] = []
        for s in preds[v]:
            for prefix in enumerate_paths(s.from_table, limit - len(out)):
                out.append(prefix + (s,))
                if len(out) >= limit:
                    return out
        return out

    result: dict[str, Path] = {}
    for v in sorted(dist):
        paths = enumerate_paths(v, 1 + _MAX_ALTERNATIVES)
        alternatives = tuple(" ; ".join(s.label for s in p) for p in paths[1:])
        result[v] = Path(v, paths[0], dist[v], ways[v] > 1, alternatives)
    return result


def _field_exists(graph: SchemaGraph, f: FieldRef) -> bool:
    t = graph.tables.get(f.table)
    return t is not None and any(c.name == f.column for c in t.columns)


def _linked_count(paths: dict[str, Path], needed: set[str]) -> int:
    seen: set[tuple[str, str]] = set()
    for t in needed:
        for s in paths[t].steps:
            if s.fan_out:
                seen.add((s.edge.name, s.from_table))
    return len(seen)


def _reason(ok: int, total: int, linked: int) -> str:
    noun = "query" if linked == 1 else "queries"
    return f"reaches {ok} of {total} fields with {linked} linked {noun}"


def resolve(
    graph: SchemaGraph, fields: Sequence[FieldRef], start_override: str | None = None
) -> Resolution:
    present = [f for f in fields if _field_exists(graph, f)]
    adj = _adjacency(graph)
    prefix = ""
    start = ""
    paths: dict[str, Path] = {}
    if start_override is not None and start_override not in graph.tables:
        prefix = f"start table '{start_override}' no longer exists; "
        start_override = None

    if start_override is not None:
        start, paths = start_override, _paths_from(start_override, adj)
        prefix = "chosen by you: "
    elif present:
        best = None
        for cand in sorted({f.table for f in present}):
            cand_paths = _paths_from(cand, adj)
            needed = {f.table for f in present if f.table in cand_paths}
            unreachable = sum(1 for f in present if f.table not in cand_paths)
            key = (
                unreachable,
                _linked_count(cand_paths, needed),
                sum(cand_paths[t].cost for t in needed),
                cand,
            )
            if best is None or key < best[0]:
                best = (key, cand, cand_paths)
        _, start, paths = best

    statuses = []
    for f in fields:
        if not _field_exists(graph, f):
            statuses.append(FieldStatus(f, "missing", None))
        elif f.table in paths:
            statuses.append(FieldStatus(f, "ok", paths[f.table]))
        else:
            statuses.append(FieldStatus(f, "unreachable", None))

    needed = {s.field.table for s in statuses if s.status == "ok"}
    reason = ""
    if start:
        ok = sum(1 for s in statuses if s.status == "ok")
        reason = prefix + _reason(ok, len(present), _linked_count(paths, needed))
    return Resolution(start, reason, paths, tuple(statuses))
