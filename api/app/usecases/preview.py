"""Read-only execution of the composed queries (role spp_reader)."""

import datetime
import decimal
import math
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from app.usecases.graph import SchemaGraph
from app.usecases.models import QueryResult
from app.usecases.planner import QuerySpec, compose


@contextmanager
def read_only_connection(pool: ConnectionPool, timeout_ms: int) -> Iterator[psycopg.Connection]:
    """A pooled connection inside a READ ONLY transaction with a statement timeout."""
    with pool.connection() as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(int(timeout_ms))))
        yield conn


def _safe(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (decimal.Decimal, uuid.UUID, datetime.timedelta)):
        return str(value)
    if isinstance(value, (datetime.date, datetime.time)):  # includes datetime
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    return str(value)


def _short(err: Exception) -> str:
    text = str(err).strip().splitlines()
    return text[0] if text else err.__class__.__name__


def _run_one(
    pool: ConnectionPool, graph: SchemaGraph, spec: QuerySpec, limit: int, timeout_ms: int
) -> QueryResult:
    merge_key = [f"{spec.root}.{child_col}" for child_col, _ in spec.merge]
    base = dict(
        id=spec.id, title=spec.title, kind=spec.kind, merge_key=merge_key,
        sql="", columns=[], rows=[], truncated=False,
    )
    try:
        with read_only_connection(pool, timeout_ms) as conn:
            base["sql"] = compose(spec, graph, limit).as_string(conn)
            cur = conn.execute(compose(spec, graph, limit + 1))
            fetched = cur.fetchall()
            columns = [d.name for d in cur.description]
        base.update(
            columns=columns,
            rows=[[_safe(v) for v in row] for row in fetched[:limit]],
            truncated=len(fetched) > limit,
        )
        return QueryResult(**base, error=None)
    except psycopg.Error as err:
        return QueryResult(**base, error=_short(err))


def run_preview(
    pool: ConnectionPool,
    graph: SchemaGraph,
    specs: Sequence[QuerySpec],
    limit: int,
    timeout_ms: int,
) -> list[QueryResult]:
    return [_run_one(pool, graph, spec, limit, timeout_ms) for spec in specs]
