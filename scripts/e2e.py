#!/usr/bin/env python3
"""End-to-end check of the SPP import API against a running stack.

  E2E_API_BASE   API base URL (default http://localhost/api)
  ADMIN_PASSWORD admin password (env, or parsed from ./.env)
  PSQL_CMD       optional, same convention as scripts/smoke.sh; enables the DB checks

Uses only the standard library. Exits non-zero on the first failed assertion.
"""
import base64
import io
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("E2E_API_BASE", "http://localhost/api").rstrip("/")
PSQL_CMD = os.environ.get("PSQL_CMD", "")
DICT_XLSX = ROOT / "docs" / "data-dict-spp.xlsx"


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg):
    print(f"ok: {msg}")


def check(cond, msg):
    if not cond:
        fail(msg)
    ok(msg)


def admin_password():
    pw = os.environ.get("ADMIN_PASSWORD", "")
    env_file = ROOT / ".env"
    if not pw and env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.startswith("ADMIN_PASSWORD="):
                pw = line.split("=", 1)[1].strip().strip("'\"")
    if not pw:
        fail("ADMIN_PASSWORD not set (environment or .env)")
    return pw


AUTH = "Basic " + base64.b64encode(f"admin:{admin_password()}".encode()).decode()


def call(method, path, body=None, ctype=None, auth=True):
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    if auth:
        req.add_header("Authorization", AUTH)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    except urllib.error.URLError as e:
        fail(f"{method} {path}: {e.reason}")
    try:
        return code, json.loads(raw)
    except ValueError:
        return code, raw.decode("utf-8", "replace")


def upload(path, data, fields=None, auth=True):
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in (fields or {}).items()]
    parts.append((f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="e2e.xlsx"\r\n'
                  "Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return call("POST", path, b"".join(parts), f"multipart/form-data; boundary={b}", auth)


def make_xlsx(sheets):
    """Tiny xlsx writer: sheets = [(name, [[str, ...], ...]), ...], all cells inline strings."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(sheets) + 1)) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, (n, _) in enumerate(sheets, 1))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, len(sheets) + 1)) + "</Relationships>")
        for i, (_, rows) in enumerate(sheets, 1):
            body = "".join("<row>" + "".join(f'<c t="inlineStr"><is><t>{escape(v)}</t></is></c>' for v in r) + "</row>" for r in rows)
            z.writestr(f"xl/worksheets/sheet{i}.xml", '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       f"<sheetData>{body}</sheetData></worksheet>")
    return buf.getvalue()


def psql(query):
    out = subprocess.run(shlex.split(PSQL_CMD) + ["-c", query], capture_output=True, text=True)
    if out.returncode != 0:
        fail(f"psql failed: {out.stderr.strip()}")
    return out.stdout.strip()


def put_proposal(job_id, proposal):
    code, body = call("PUT", f"/imports/{job_id}/proposal", json.dumps(proposal).encode(), "application/json")
    return code, body


def new_dictionary_job():
    code, body = upload("/imports", DICT_XLSX.read_bytes(), {"mode": "dictionary"})
    check(code == 201, f"POST /imports (dictionary) returns 201 (got {code})")
    return body


def prepare_and_apply(body):
    """PUT the proposal unchanged (it must pass generate_ddl), then apply."""
    job_id = body["id"]
    code, res = put_proposal(job_id, body["proposal"])
    check(code == 200, f"PUT proposal accepted unedited (got {code}: {res if code != 200 else ''})")
    return call("POST", f"/imports/{job_id}/apply")


def main():
    names = {"sppProject", "sppProjectAddition", "sppProjectConnection", "sppProjectAddress", "sppBusinessPartner"}

    print("== (1) dictionary upload")
    body = new_dictionary_job()
    proposal = body["proposal"]
    check(proposal["schemaOnly"] is True, "proposal.schemaOnly is true")
    table_names = {t["name"] for t in proposal["tables"]}
    check(names <= table_names, f"proposal contains {sorted(names)}")
    print(f"  {len(table_names)} tables, {len(proposal['foreignKeys'])} foreign keys, {len(body['issues'])} issues")
    check(any(i["sheet"] == "sppDocProcExtendScod" and i["column"] == "documentDate" and "boolean" in i["message"]
              for i in body["issues"]), "issues include the boolean documentDate problem of sppDocProcExtendScod")

    print("== (2) foreign key")
    check(any(f["table"] == "sppProject" and f["refTable"] == "sppBusinessPartner" and f["accepted"]
              for f in proposal["foreignKeys"]), "sppProject has an accepted FK to sppBusinessPartner")

    print("== (3) apply")
    code, res = prepare_and_apply(body)
    check(code == 200, f"apply returns 200 (got {code}: {res if code != 200 else ''})")
    check(len(res["created"]) == len(proposal["tables"]) and set(res["created"]) == table_names
          and not res["skipped"] and not res["issues"],
          f"created lists all {len(table_names)} tables of the proposal")
    cols = {t["name"]: {c["name"] for c in t["columns"]} for t in proposal["tables"]}
    expected_fks = sum(1 for f in proposal["foreignKeys"] if f["accepted"] and f["table"] in cols
                       and f["refTable"] in cols and f["column"] in cols[f["table"]]
                       and f["refColumn"] in cols[f["refTable"]])

    print("== (4) database")
    if PSQL_CMD:
        check(psql("SELECT to_regclass('spp.\"sppProject\"') IS NOT NULL") == "t", 'spp."sppProject" exists')
        check(psql("SELECT count(*) FROM pg_constraint WHERE contype='f' AND conrelid='spp.\"sppProject\"'::regclass "
                   "AND confrelid='spp.\"sppBusinessPartner\"'::regclass") == "1",
              "sppProject has a foreign key constraint to sppBusinessPartner")
        check(psql("SELECT has_table_privilege('spp_reader','spp.\"sppProject\"','SELECT')") == "t",
              "spp_reader has SELECT on sppProject")
        check(psql("SELECT count(*) FROM pg_constraint WHERE contype='f' AND connamespace='spp'::regnamespace")
              == str(expected_fks), f"spp has {expected_fks} foreign key constraints (accepted FKs with a table in the proposal)")
    else:
        print("skip: PSQL_CMD not set, database checks skipped")

    print("== (5) re-apply")
    code, res = call("POST", f"/imports/{body['id']}/apply")
    check(code == 409, f"applying the same job again returns 409 (got {code})")
    code, res = prepare_and_apply(new_dictionary_job())
    check(code == 200, f"second upload+apply returns 200 (got {code})")
    check(not res["created"] and set(res["skipped"]) == table_names,
          "second apply reports every table in skipped")

    print("== (6) load data")
    p1, p2, b1, b2 = (str(uuid.uuid4()) for _ in range(4))
    data = make_xlsx([
        ("sppProject", [["sppProjectID", "sppBusinessPartner", "projectCode"],
                        [p1, b1, "E2E-1"], [p2, b2, "E2E-2"]]),
        ("sppBusinessPartner", [["businessPartnerID", "taxID"], [b1, "1111111111111"], [b2, "2222222222222"]]),
    ])
    code, res = upload("/loads", data)
    check(code == 200, f"POST /loads returns 200 (got {code}: {res if code != 200 else ''})")
    check(res["rowsLoaded"] == {"sppProject": 2, "sppBusinessPartner": 2}, f"rowsLoaded is 2 and 2 ({res['rowsLoaded']})")

    print("== (7) repeat load")
    counts = lambda: psql('SELECT (SELECT count(*) FROM spp."sppProject")||\',\'||(SELECT count(*) FROM spp."sppBusinessPartner")')
    before = counts() if PSQL_CMD else None
    code, res = upload("/loads", data)
    check(code == 422, f"same load again returns 422 (got {code})")
    check(any("duplicate key" in i["message"] or "primary key" in i["message"].lower() or "pkey" in i["message"]
              for i in res["issues"]), f"issue reports a primary-key violation ({res['issues'][:1]})")
    if PSQL_CMD:
        check(counts() == before, f"row counts unchanged ({before})")

    print("== (8) authentication")
    probe = "00000000-0000-0000-0000-000000000000"
    for method, path in [("GET", f"/imports/{probe}"), ("PUT", f"/imports/{probe}/proposal"),
                         ("POST", f"/imports/{probe}/apply")]:
        code, _ = call(method, path, b"{}" if method == "PUT" else None, "application/json", auth=False)
        check(code == 401, f"{method} {path} without credentials is 401 (got {code})")
    for path in ("/imports", "/loads"):
        code, _ = upload(path, data, auth=False)
        check(code == 401, f"POST {path} without credentials is 401 (got {code})")
    code, res = call("GET", "/healthz", auth=False)
    check(code == 200 and res == {"status": "ok"}, "GET /healthz is open")
    print("e2e: all checks passed")


if __name__ == "__main__":
    main()
