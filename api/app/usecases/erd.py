"""ERD data of one use case: tables, used foreign keys and suggested keys."""

from collections.abc import Sequence

from app.usecases.graph import SchemaGraph
from app.usecases.models import Erd, ErdEdge, ErdNode, Recommendation
from app.usecases.resolver import Resolution, Step


def _cardinality(step: Step) -> str:
    if step.fan_out:
        return "one-to-many"
    if step.edge.one_to_one:
        return "one-to-one"
    return "many-to-one"


def build_erd(
    graph: SchemaGraph, resolution: Resolution, recommendations: Sequence[Recommendation]
) -> Erd:
    if not resolution.start:
        return Erd(nodes=[], edges=[])
    chosen: dict[str, list[str]] = {}
    steps: dict[tuple[str, str, str], Step] = {}
    for s in resolution.fields:
        if s.status == "missing":
            continue
        cols = chosen.setdefault(s.field.table, [])
        if s.field.column not in cols:
            cols.append(s.field.column)
        if s.status == "ok":
            for step in s.path.steps:
                steps.setdefault((step.edge.name, step.from_table, step.to_table), step)

    tables = {resolution.start} | set(chosen) | {t for k in steps for t in (k[1], k[2])}
    suggested = [r.fix for r in recommendations if r.fix is not None]
    tables |= {t for f in suggested for t in (f.table, f.ref_table)}

    def role(table: str) -> str:
        if table == resolution.start:
            return "start"
        return "reached" if table in resolution.paths else "unreachable"

    nodes = [ErdNode(table=t, role=role(t), fields=chosen.get(t, [])) for t in sorted(tables)]
    edges = [
        ErdEdge(
            from_=st.from_table, to=st.to_table, columns=list(st.edge.child_cols),
            status="fk", cardinality=_cardinality(st),
        )
        for st in steps.values()
    ]
    edges += [
        ErdEdge(
            from_=f.table, to=f.ref_table, columns=[f.column],
            status="suggested", cardinality="many-to-one",
        )
        for f in suggested
    ]
    return Erd(nodes=nodes, edges=edges)
