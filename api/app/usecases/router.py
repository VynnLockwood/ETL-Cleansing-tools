"""HTTP routes of the use case checker."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from app.usecases.graph import load_graph


def _reader_pool(request: Request):
    pool = request.app.state.reader_pool
    if pool is None:
        raise HTTPException(status_code=503, detail="reader database not configured")
    return pool


def build_router(admin: Callable) -> APIRouter:
    router = APIRouter(dependencies=[Depends(admin)])

    @router.get("/schema/tables")
    def schema_tables(request: Request) -> list[dict]:
        pool = _reader_pool(request)
        with pool.connection() as conn:
            graph = load_graph(conn)
        return [
            {
                "name": t.name,
                "columns": [
                    {
                        "name": c.name,
                        "sqlType": c.sql_type,
                        "isPrimaryKey": c.is_pk,
                        "isForeignKey": c.is_fk,
                    }
                    for c in t.columns
                ],
            }
            for t in graph.tables.values()
        ]

    return router
