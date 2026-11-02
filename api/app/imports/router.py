"""HTTP endpoints for the Excel import workflow."""

import io
import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.imports.apply import apply as apply_import
from app.imports.ddl import DdlError, generate_ddl
from app.imports.dictionary import parse_dictionary
from app.imports.infer import profile_sheet
from app.imports.keys import detect_keys
from app.imports.load import load_existing
from app.imports.models import Issue, Proposal, Workbook
from app.imports.workbook import read_workbook

log = logging.getLogger(__name__)
_EDITABLE = ("proposed", "failed")


def _job_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=404, detail="Import not found") from None


def _issues_json(issues: list[Issue]) -> list[dict]:
    return [i.model_dump(by_alias=True, mode="json") for i in issues]


def _store_issues(conn, job_id: uuid.UUID, issues: list[Issue], stage: str = "upload") -> None:
    if not issues:
        return
    with conn.cursor() as cur:
        cur.executemany(
            'INSERT INTO app.import_issue (job_id, sheet, "column", row, message, stage) '
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [(job_id, i.sheet, i.column, i.row, i.message, stage) for i in map(_clean_issue, issues)],
        )


def _load_issues(conn, job_id: uuid.UUID) -> list[dict]:
    rows = conn.execute(
        'SELECT sheet, "column", row, message FROM app.import_issue '
        "WHERE job_id = %s ORDER BY id",
        (job_id,),
    ).fetchall()
    return [{"sheet": r[0], "column": r[1], "row": r[2], "message": r[3]} for r in rows]


def _clean(text: str) -> str:
    """Postgres text cannot hold NUL characters."""
    return text.replace("\x00", "")


def _clean_issue(i: Issue) -> Issue:
    return Issue(sheet=_clean(i.sheet), column=_clean(i.column), row=i.row, message=_clean(i.message))


def _fail_job(pool, jid: uuid.UUID, exc: BaseException) -> None:
    """Best effort: record a generic failure so the job never stays 'applying'."""
    try:
        with pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO app.import_issue (job_id, sheet, \"column\", row, message, stage) "
                "VALUES (%s, '', '', 0, %s, 'apply')",
                (jid, f"could not record the apply result: {type(exc).__name__}"),
            )
            conn.execute("UPDATE app.import_job SET status = 'failed' WHERE id = %s", (jid,))
    except Exception:
        log.exception("could not mark import %s as failed", jid)


def _issue(message: str) -> Issue:
    return Issue(sheet="", column="", row=0, message=message)


def _get_job(conn, job_id: uuid.UUID) -> tuple[str, dict | None]:
    row = conn.execute(
        "SELECT status, proposal FROM app.import_job WHERE id = %s", (job_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return row[0], row[1]


def _read_upload(data: bytes) -> tuple[Workbook, list[Issue]]:
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    try:
        return read_workbook(io.BytesIO(data))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is not a readable .xlsx workbook",
        ) from None


def _finish_job(pool, jid: uuid.UUID, result, issues: list[Issue]) -> JSONResponse:
    """Record the outcome (never leaving the job 'applying') and build the response."""
    issues = [_clean_issue(i) for i in issues]
    failed = bool(issues) or result is None
    try:
        with pool.connection() as conn, conn.transaction():
            if failed:
                _store_issues(conn, jid, issues, stage="apply")
            conn.execute(
                "UPDATE app.import_job SET status = %s WHERE id = %s",
                ("failed" if failed else "applied", jid),
            )
    except BaseException as e:
        _fail_job(pool, jid, e)
        raise
    if failed:
        return JSONResponse(status_code=422, content={"issues": _issues_json(issues)})
    return JSONResponse(content=result.model_dump(by_alias=True, mode="json"))


def build_router(admin: Callable) -> APIRouter:
    router = APIRouter(dependencies=[Depends(admin)])

    @router.post("/imports", status_code=201)
    def create_import(
        request: Request,
        file: UploadFile = File(...),
        mode: str = Form(""),
    ):
        data = file.file.read()
        wb, issues = _read_upload(data)
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
        try:
            generate_ddl(body)
        except DdlError as e:
            return JSONResponse(status_code=422, content={"issues": _issues_json([_issue(str(e))])})
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
        # Atomic claim: only one request can move a job from proposed/failed to applying.
        with pool.connection() as conn, conn.transaction():
            claimed = conn.execute(
                "UPDATE app.import_job SET status = 'applying' "
                "WHERE id = %s AND status IN ('proposed', 'failed') AND proposal IS NOT NULL "
                "RETURNING proposal",
                (jid,),
            ).fetchone()
            if claimed is None:
                status, _ = _get_job(conn, jid)  # 404 if unknown
                detail = (
                    "Import was already applied"
                    if status == "applied"
                    else f"Import is {status}; it cannot be applied"
                )
                raise HTTPException(status_code=409, detail=detail)
            conn.execute(
                "DELETE FROM app.import_issue WHERE job_id = %s AND stage = 'apply'", (jid,)
            )
        proposal_json = claimed[0]

        path = Path(request.app.state.cfg.upload_dir) / f"{jid}.xlsx"
        result = None
        try:
            proposal = Proposal.model_validate(proposal_json)
            wb, _ = read_workbook(io.BytesIO(path.read_bytes()))
            result = apply_import(pool, proposal, wb)
            issues = result.issues
        except psycopg.Error as e:
            issues = [_issue(str(e).strip() or type(e).__name__)]
        except DdlError as e:
            issues = [_issue(str(e))]
        except OSError:
            issues = [_issue("The uploaded file is missing")]
        except ValueError as e:  # includes pydantic.ValidationError
            issues = [_issue(f"Invalid proposal: {e}")]
        except BaseException as e:
            _fail_job(pool, jid, e)
            raise

        return _finish_job(pool, jid, result, issues)

    @router.post("/loads")
    def load_data(request: Request, file: UploadFile = File(...)):
        wb, _ = _read_upload(file.file.read())
        pool = request.app.state.pool
        jid = uuid.uuid4()
        with pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO app.import_job (id, filename, status, proposal) "
                "VALUES (%s, %s, 'applying', NULL)",
                (jid, file.filename),
            )
        result = None
        try:
            result = load_existing(pool, wb)
            issues = result.issues
        except psycopg.Error as e:
            issues = [_issue(str(e).strip() or type(e).__name__)]
        except BaseException as e:
            _fail_job(pool, jid, e)
            raise

        return _finish_job(pool, jid, result, issues)

    return router
