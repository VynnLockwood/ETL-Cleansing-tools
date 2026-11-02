import io
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook as XlWorkbook

from app.config import Config
from app.main import create_app

DICT_XLSX = Path(__file__).resolve().parents[3] / "docs" / "data-dict-spp.xlsx"


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = XlWorkbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def people_xlsx() -> bytes:
    return make_xlsx(
        {
            "people": [["id", "name"], [1, "Ann"], [2, "Bob"], [3, "Cy"]],
            "pets": [["pid", "owner_id"], [1, 1], [2, 2]],
        }
    )


@pytest.fixture
def cfg(clean_db, tmp_path):
    return Config(clean_db, clean_db, "pw", str(tmp_path / "uploads"))


@pytest.fixture
def client(cfg):
    with TestClient(create_app(cfg)) as c:
        c.auth = ("admin", "pw")
        yield c


def upload(client, data: bytes, filename="book.xlsx", **form):
    return client.post("/imports", files={"file": (filename, data)}, data=form)


def spp_tables(url):
    with psycopg.connect(url) as conn:
        return {r[0] for r in conn.execute(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'spp'")}


def test_upload_creates_proposal(client, cfg):
    r = upload(client, people_xlsx())
    assert r.status_code == 201
    body = r.json()
    assert {t["name"] for t in body["proposal"]["tables"]} == {"people", "pets"}
    assert body["proposal"]["schemaOnly"] is False
    assert body["issues"] == []
    assert (Path(cfg.upload_dir) / f"{body['id']}.xlsx").exists()
    got = client.get(f"/imports/{body['id']}").json()
    assert got["status"] == "proposed"
    assert got["existingTables"] == []
    with psycopg.connect(cfg.database_url) as conn:
        assert conn.execute("SELECT filename FROM app.import_job").fetchone()[0] == "book.xlsx"


def test_put_proposal_persists(client):
    job = upload(client, people_xlsx()).json()
    proposal = job["proposal"]
    proposal["addAudit"] = True
    r = client.put(f"/imports/{job['id']}/proposal", json=proposal)
    assert r.status_code == 200
    assert r.json()["addAudit"] is True
    assert client.get(f"/imports/{job['id']}").json()["proposal"]["addAudit"] is True


def test_apply_happy_path(client, cfg):
    job = upload(client, people_xlsx()).json()
    r = client.post(f"/imports/{job['id']}/apply")
    assert r.status_code == 200
    body = r.json()
    assert set(body["created"]) == {"people", "pets"}
    assert body["rowsLoaded"] == {"people": 3, "pets": 2}
    assert spp_tables(cfg.database_url) == {"people", "pets"}
    got = client.get(f"/imports/{job['id']}").json()
    assert got["status"] == "applied"
    assert set(got["existingTables"]) == {"people", "pets"}
    assert client.post(f"/imports/{job['id']}/apply").status_code == 409


def test_apply_failure_stores_issues(client, cfg):
    job = upload(client, make_xlsx({"t": [["id", "v"], [1, "a"], [2, "b"]]})).json()
    proposal = job["proposal"]
    for c in proposal["tables"][0]["columns"]:
        if c["name"] == "v":
            c["sqlType"] = "integer"
    assert client.put(f"/imports/{job['id']}/proposal", json=proposal).status_code == 200
    r = client.post(f"/imports/{job['id']}/apply")
    assert r.status_code == 422
    assert len(r.json()["issues"]) == 2
    assert r.json()["issues"][0]["column"] == "v"
    got = client.get(f"/imports/{job['id']}").json()
    assert got["status"] == "failed"
    assert len(got["issues"]) == 2
    with psycopg.connect(cfg.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM app.import_issue").fetchone()[0] == 2
    assert spp_tables(cfg.database_url) == set()
    # failed jobs may be corrected and re-applied
    proposal["tables"][0]["columns"][1]["sqlType"] = "text"
    assert client.put(f"/imports/{job['id']}/proposal", json=proposal).status_code == 200
    assert client.post(f"/imports/{job['id']}/apply").status_code == 200


def test_unknown_job_404(client):
    missing = uuid.uuid4()
    assert client.get(f"/imports/{missing}").status_code == 404
    assert client.put(f"/imports/{missing}/proposal",
                      json={"tables": [], "foreignKeys": []}).status_code == 404
    assert client.post(f"/imports/{missing}/apply").status_code == 404


def test_malformed_uuid_is_404(client):
    assert client.get("/imports/not-a-uuid").status_code == 404
    assert client.post("/imports/not-a-uuid/apply").status_code == 404
    assert client.put("/imports/not-a-uuid/proposal",
                      json={"tables": [], "foreignKeys": []}).status_code == 404


def test_corrupt_upload_is_400(client):
    r = upload(client, b"this is not a zip")
    assert r.status_code == 400
    assert r.json()["detail"]
    r = upload(client, b"")
    assert r.status_code == 400


def test_put_after_applied_is_409(client):
    job = upload(client, people_xlsx()).json()
    assert client.post(f"/imports/{job['id']}/apply").status_code == 200
    r = client.put(f"/imports/{job['id']}/proposal", json=job["proposal"])
    assert r.status_code == 409


def test_workbook_issues_returned_and_stored(client, cfg):
    r = upload(client, make_xlsx({"t": [["id", ""], [1, "x"]]}))
    assert r.status_code == 201
    assert any(i["message"] == "blank header" for i in r.json()["issues"])
    assert client.get(f"/imports/{r.json()['id']}").json()["issues"]


def test_dictionary_mode_upload_returns_schema_only_proposal(client):
    r = upload(client, DICT_XLSX.read_bytes(), filename="dict.xlsx", mode="dictionary")
    assert r.status_code == 201
    body = r.json()
    assert body["proposal"]["schemaOnly"] is True
    assert body["proposal"]["tables"]
    assert body["issues"]


def test_apply_schema_only_creates_tables_without_rows(client, cfg):
    data = make_xlsx(
        {
            "Dictionary": [
                ["Table", "Column", "Description", "Type", "Notes"],
                ["widget", "id", "key", "integer", "PK"],
                ["widget", "label", "name", "text", ""],
            ]
        }
    )
    r = upload(client, data, mode="dictionary")
    assert r.status_code == 201, r.text
    assert r.json()["proposal"]["schemaOnly"] is True
    jid = r.json()["id"]
    r = client.post(f"/imports/{jid}/apply")
    assert r.status_code == 200, r.text
    assert r.json()["created"] == ["widget"]
    assert r.json()["rowsLoaded"] == {}
    with psycopg.connect(cfg.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM spp.widget").fetchone()[0] == 0


def test_routes_require_auth(client):
    jid = uuid.uuid4()
    client.auth = None
    assert client.post("/imports", files={"file": ("a.xlsx", b"x")}).status_code == 401
    assert client.get(f"/imports/{jid}").status_code == 401
    assert client.put(f"/imports/{jid}/proposal",
                      json={"tables": [], "foreignKeys": []}).status_code == 401
    assert client.post(f"/imports/{jid}/apply").status_code == 401
    assert client.get("/healthz").status_code == 200
