"""HTTP routes of the use case checker."""

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.imports.models import Issue
from app.usecases import store
from app.usecases.graph import load_graph
from app.usecases.models import UseCase, UseCaseIn


def _reader_pool(request: Request):
    pool = request.app.state.reader_pool
    if pool is None:
        raise HTTPException(status_code=503, detail="reader database not configured")
    return pool


def _uc_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=404, detail="use case not found") from None


def _issues_response(issues: list[Issue]) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"issues": [i.model_dump(by_alias=True, mode="json") for i in issues]},
    )


def _validate(request: Request, body: UseCaseIn) -> list[Issue]:
    # Reading the catalog needs no write rights; prefer the read-only pool when present.
    pool = request.app.state.reader_pool or request.app.state.pool
    with pool.connection() as conn:
        graph = load_graph(conn)
    return store.validate(graph, body)


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

    @router.get("/usecases", response_model=list[UseCase])
    def list_use_cases(request: Request):
        return store.list_all(request.app.state.pool)

    @router.post("/usecases", status_code=201, response_model=UseCase)
    def create_use_case(body: UseCaseIn, request: Request):
        issues = _validate(request, body)
        if issues:
            return _issues_response(issues)
        return store.create(request.app.state.pool, body)

    @router.get("/usecases/{use_case_id}", response_model=UseCase)
    def get_use_case(use_case_id: str, request: Request):
        uc = store.get(request.app.state.pool, _uc_id(use_case_id))
        if uc is None:
            raise HTTPException(status_code=404, detail="use case not found")
        return uc

    @router.put("/usecases/{use_case_id}", response_model=UseCase)
    def update_use_case(use_case_id: str, body: UseCaseIn, request: Request):
        uid = _uc_id(use_case_id)
        issues = _validate(request, body)
        if issues:
            return _issues_response(issues)
        uc = store.update(request.app.state.pool, uid, body)
        if uc is None:
            raise HTTPException(status_code=404, detail="use case not found")
        return uc

    @router.delete("/usecases/{use_case_id}", status_code=204)
    def delete_use_case(use_case_id: str, request: Request):
        if not store.delete(request.app.state.pool, _uc_id(use_case_id)):
            raise HTTPException(status_code=404, detail="use case not found")
        return Response(status_code=204)

    return router
