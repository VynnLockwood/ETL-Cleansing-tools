"""HTTP endpoints for the Excel import workflow."""

import io
import json
import uuid
from collections.abc import Callable
from pathlib import Path

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.imports.apply import apply as apply_import
from app.imports.dictionary import parse_dictionary
from app.imports.infer import profile_sheet
from app.imports.keys import detect_keys
from app.imports.models import Issue, Proposal
from app.imports.workbook import read_workbook

_EDITABLE = ("proposed", "failed")


def _job_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=404, detail="Import not found") from None


def _issues_json(issues: list[Issue]) -> list[dict]:
    return [i.model_dump(by_alias=True, mode="json") for i in issues]


def _store_issues(conn, job_id: uuid.UUID, issues: list[Issue]) -> None:
    if not issues:
        return
    with conn.cursor() as cur:
        cur.executemany(
            'INSERT INTO app.import_issue (job_id, sheet, "column", row, message) '
            "VALUES (%s, %s, %s, %s, %s)",
            [(job_id, i.sheet, i.column, i.row, i.message) for i in issues],
        )


def _load_issues(conn, job_id: uuid.UUID) -> list[dict]:
    rows = conn.execute(
        'SELECT sheet, "column", row, message FROM app.import_issue '
        "WHERE job_id = %s ORDER BY id",
        (job_id,),
    ).fetchall()
    return [{"sheet": r[0], "column": r[1], "row": r[2], "message": r[3]} for r in rows]


def _get_job(conn, job_id: uuid.UUID) -> tuple[str, dict | None]:
    row = conn.execute(
        "SELECT status, proposal FROM app.import_job WHERE id = %s", (job_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return row[0], row[1]


def build_router(admin: Callable) -> APIRouter:
    router = APIRouter(dependencies=[Depends(admin)])

    @router.post("/imports", status_code=201)
    def create_import(
        request: Request,
        file: UploadFile = File(...),
        mode: str = Form(""),
    ):
        data = file.file.read()
        if not data:
            raise HTTPException(status_code=400, detail="The uploaded file is empty")
        try:
            wb, issues = read_workbook(io.BytesIO(data))
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is not a readable .xlsx workbook",
            ) from None
        if mode == "dictionary":
            proposal, dict_issues = parse_dictionary(wb)
            issues = issues + dict_issues
        else:
            profiles = [profile_sheet(s) for s in wb.sheets]
            proposal = detect_keys(wb, profiles, threshold=0.99)

        job_id = uuid.uuid4()
        upload_dir = Path(request.app.state.cfg.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        (upload_dir / f"{job_id}.xlsx").write_bytes(data)

        proposal_json = proposal.model_dump(by_alias=True, mode="json")
        with request.app.state.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO app.import_job (id, filename, status, proposal) "
                "VALUES (%s, %s, 'proposed', %s::jsonb)",
                (job_id, file.filename, json.dumps(proposal_json)),
            )
            _store_issues(conn, job_id, issues)
        return JSONResponse(
            status_code=201,
            content={"id": str(job_id), "proposal": proposal_json, "issues": _issues_json(issues)},
        )

    @router.get("/imports/{job_id}")
    def get_import(job_id: str, request: Request):
        jid = _job_id(job_id)
        with request.app.state.pool.connection() as conn:
            status, proposal = _get_job(conn, jid)
            issues = _load_issues(conn, jid)
            names = [t["name"] for t in (proposal or {}).get("tables", [])]
            existing: list[str] = []
            if names:
                present = {
                    r[0]
                    for r in conn.execute(
                        "SELECT tablename FROM pg_catalog.pg_tables "
                        "WHERE schemaname = 'spp' AND tablename = ANY(%s)",
                        (names,),
                    )
                }
                existing = [n for n in names if n in present]
        return {
            "id": str(jid),
            "status": status,
            "proposal": proposal,
            "issues": issues,
            "existingTables": existing,
        }

    @router.put("/imports/{job_id}/proposal")
    def put_proposal(job_id: str, body: Proposal, request: Request):
        jid = _job_id(job_id)
        proposal_json = body.model_dump(by_alias=True, mode="json")
        with request.app.state.pool.connection() as conn, conn.transaction():
            status, _ = _get_job(conn, jid)
            if status not in _EDITABLE:
                raise HTTPException(
                    status_code=409, detail=f"Import is {status}; its proposal can no longer change"
                )
            conn.execute(
                "UPDATE app.import_job SET proposal = %s::jsonb WHERE id = %s",
                (json.dumps(proposal_json), jid),
            )
        return proposal_json

    @router.post("/imports/{job_id}/apply")
    def apply_job(job_id: str, request: Request):
        jid = _job_id(job_id)
        pool = request.app.state.pool
        with pool.connection() as conn:
            status, proposal_json = _get_job(conn, jid)
        if status == "applied":
            raise HTTPException(status_code=409, detail="Import was already applied")
        if status not in _EDITABLE or proposal_json is None:
            raise HTTPException(status_code=409, detail=f"Import is {status}; it cannot be applied")

        proposal = Proposal.model_validate(proposal_json)
        path = Path(request.app.state.cfg.upload_dir) / f"{jid}.xlsx"
        try:
            wb, _ = read_workbook(io.BytesIO(path.read_bytes()))
            result = apply_import(pool, proposal, wb)
            issues = result.issues
        except psycopg.Error as e:
            result = None
            issues = [Issue(sheet="", column="", row=0, message=str(e).strip() or type(e).__name__)]
        except OSError:
            result = None
            issues = [Issue(sheet="", column="", row=0, message="The uploaded file is missing")]

        with pool.connection() as conn, conn.transaction():
            if issues or result is None:
                _store_issues(conn, jid, issues)
                conn.execute("UPDATE app.import_job SET status = 'failed' WHERE id = %s", (jid,))
            else:
                conn.execute("UPDATE app.import_job SET status = 'applied' WHERE id = %s", (jid,))
        if issues or result is None:
            return JSONResponse(status_code=422, content={"issues": _issues_json(issues)})
        return JSONResponse(content=result.model_dump(by_alias=True, mode="json"))

    return router
