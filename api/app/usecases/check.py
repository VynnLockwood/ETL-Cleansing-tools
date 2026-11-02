"""One use case test: resolve, plan, preview, recommend, draw."""

from psycopg_pool import ConnectionPool

from app.config import Config
from app.usecases.erd import build_erd
from app.usecases.graph import load_graph
from app.usecases.models import FieldResult, TestResult, UseCase
from app.usecases.planner import plan_queries
from app.usecases.preview import run_preview
from app.usecases.recommend import recommend
from app.usecases.resolver import resolve


def run_check(reader_pool: ConnectionPool, cfg: Config, uc: UseCase) -> TestResult:
    with reader_pool.connection() as conn:
        graph = load_graph(conn)
    resolution = resolve(graph, uc.fields, uc.start_table)
    specs = plan_queries(graph, resolution)
    queries = run_preview(reader_pool, graph, specs, cfg.preview_limit, cfg.preview_timeout_ms)
    recommendations = recommend(reader_pool, graph, resolution, specs, cfg.preview_timeout_ms)

    query_of = {it.alias: spec.id for spec in specs for it in spec.select}
    fields = []
    for s in resolution.fields:
        f = s.field
        if s.status == "ok":
            fields.append(
                FieldResult(
                    table=f.table, column=f.column, status="ok",
                    query=query_of.get(f"{f.table}.{f.column}"),
                    via=[step.label for step in s.path.steps],
                )
            )
        elif s.status == "unreachable":
            fields.append(
                FieldResult(
                    table=f.table, column=f.column, status="unreachable",
                    reason=f"no foreign key path from {resolution.start}",
                )
            )
        else:
            fields.append(
                FieldResult(
                    table=f.table, column=f.column, status="missing",
                    reason="table or column no longer exists",
                )
            )
    return TestResult(
        start_table=resolution.start,
        start_table_reason=resolution.start_reason,
        fields=fields,
        queries=queries,
        recommendations=recommendations,
        erd=build_erd(graph, resolution, recommendations),
    )
