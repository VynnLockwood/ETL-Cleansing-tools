import io

import psycopg
import pytest
from openpyxl import Workbook as XlWorkbook
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app import db
from app.imports.apply import apply as apply_import
from app.imports.dictionary import parse_dictionary
from app.imports.load import load_existing
from app.imports.models import Sheet, Workbook
from app.imports.workbook import read_workbook


def _dsn(owner_url: str, role: str, password: str) -> str:
    params = conninfo_to_dict(owner_url)
    params["user"] = role
    params["password"] = password
    return make_conninfo(**params)


@pytest.fixture
def pool(clean_db):
    db.migrate(clean_db)
    p = db.open_pool(_dsn(clean_db, "spp_api", "apipw"))
    with p.connection() as conn:
        conn.execute("CREATE TABLE spp.dept (id integer PRIMARY KEY, name varchar(10), "
                     "budget numeric(8,2), opened date, extra text DEFAULT 'dflt')")
        conn.execute("CREATE TABLE spp.emp (id integer PRIMARY KEY, "
                     "dept_id integer NOT NULL REFERENCES spp.dept (id), at timestamptz)")
        conn.execute("CREATE TABLE spp.blobby (id integer, data jsonb)")
    yield p
    p.close()


def count(pool, table):
    with pool.connection() as conn:
        return conn.execute(f'SELECT count(*) FROM spp."{table}"').fetchone()[0]


def dept_sheet(rows=None):
    return Sheet("dept", ["id", "name", "budget", "opened"],
                 rows if rows is not None else [["1", "Ops", "10.50", "2024-01-31"], ["2", "HR", " ", ""]])


def emp_sheet(rows=None):
    return Sheet("emp", ["id", "dept_id", "at"],
                 rows if rows is not None else [["1", "1", "2024-01-31T10:00:00"], ["2", "2", ""]])


def test_loads_rows_into_existing_table(pool):
    r = load_existing(pool, Workbook(sheets=[dept_sheet()]))
    assert r.issues == [] and r.rows_loaded == {"dept": 2}
    with pool.connection() as conn:
        rows = conn.execute("SELECT id, name, budget, opened, extra FROM spp.dept ORDER BY id").fetchall()
    assert rows[0][4] == "dflt" and rows[1][2] is None and rows[1][3] is None
    assert str(rows[0][2]) == "10.50"


def test_unknown_column_is_issue(pool):
    s = Sheet("dept", ["id", "bogus"], [["1", "x"]])
    r = load_existing(pool, Workbook(sheets=[s]))
    assert [(i.sheet, i.column, i.row) for i in r.issues] == [("dept", "bogus", 1)]
    assert r.rows_loaded == {} and count(pool, "dept") == 0


def test_unknown_sheet_is_issue(pool):
    r = load_existing(pool, Workbook(sheets=[Sheet("nope", ["a"], [["1"]]), dept_sheet()]))
    assert [i.message for i in r.issues] == ["no table named 'nope' in schema spp"]
    assert count(pool, "dept") == 0


def test_app_schema_is_not_loadable(pool):
    r = load_existing(pool, Workbook(sheets=[Sheet("import_job", ["id"], [["1"]])]))
    assert len(r.issues) == 1 and "no table named" in r.issues[0].message


def test_bad_value_reports_row_and_column(pool):
    r = load_existing(pool, Workbook(sheets=[dept_sheet([["1", "Ops", "", ""], ["x", "HR", "", ""]])]))
    assert [(i.sheet, i.column, i.row) for i in r.issues] == [("dept", "id", 3)]
    assert count(pool, "dept") == 0


def test_unsupported_column_type_is_issue(pool):
    r = load_existing(pool, Workbook(sheets=[Sheet("blobby", ["id", "data"], [["1", "{}"]])]))
    assert [(i.sheet, i.column) for i in r.issues] == [("blobby", "data")]
    assert "jsonb" in r.issues[0].message and count(pool, "blobby") == 0


def test_rolls_back_on_primary_key_conflict(pool):
    wb = Workbook(sheets=[dept_sheet(), emp_sheet()])
    assert load_existing(pool, wb).rows_loaded == {"dept": 2, "emp": 2}
    r = load_existing(pool, wb)
    assert r.rows_loaded == {} and len(r.issues) == 1
    assert "duplicate key" in r.issues[0].message and "Traceback" not in r.issues[0].message
    assert count(pool, "dept") == 2 and count(pool, "emp") == 2


def test_loads_in_dependency_order(pool):
    r = load_existing(pool, Workbook(sheets=[emp_sheet(), dept_sheet()]))
    assert r.issues == [] and r.rows_loaded == {"dept": 2, "emp": 2}


def test_foreign_key_violation_rolls_back_everything(pool):
    r = load_existing(pool, Workbook(sheets=[dept_sheet(), emp_sheet([["1", "99", ""]])]))
    assert r.rows_loaded == {} and len(r.issues) == 1 and "foreign key" in r.issues[0].message
    assert count(pool, "dept") == 0


def test_dictionary_then_data_flow(clean_db):
    db.migrate(clean_db)
    pool = db.open_pool(_dsn(clean_db, "spp_api", "apipw"))
    try:
        dict_wb = XlWorkbook()
        ws = dict_wb.active
        ws.title = "Dictionary"
        ws.append(["Table", "Column", "Description", "Data type", "Note"])
        ws.append(["partner", "partnerID", "", "integer", ""])
        ws.append(["partner", "name", "", "character varying(50)", ""])
        ws.append(["project", "projectID", "", "integer", ""])
        ws.append(["project", "partnerID", "", "integer", "FK → partner.partnerID"])
        ws.append(["project", "budget", "", "numeric(10,2)", ""])
        buf = io.BytesIO()
        dict_wb.save(buf)
        buf.seek(0)
        wb, _ = read_workbook(buf)
        proposal, _ = parse_dictionary(wb)
        proposal.schema_only = True
        created = apply_import(pool, proposal, wb)
        assert created.created == ["partner", "project"] and created.rows_loaded == {}

        data = Workbook(sheets=[
            Sheet("project", ["projectID", "partnerID", "budget"],
                  [["1", "1", "100.25"], ["2", "2", "5"], ["3", "1", ""]]),
            Sheet("partner", ["partnerID", "name"], [["1", "Acme"], ["2", "Beta"]]),
        ])
        r = load_existing(pool, data)
        assert r.issues == [] and r.rows_loaded == {"partner": 2, "project": 3}
        with pool.connection() as conn:
            orphans = conn.execute(
                'SELECT count(*) FROM spp.project p LEFT JOIN spp.partner a '
                'ON a."partnerID" = p."partnerID" WHERE a."partnerID" IS NULL').fetchone()[0]
        assert orphans == 0

        again = load_existing(pool, data)
        assert again.rows_loaded == {} and len(again.issues) == 1
        assert "duplicate key" in again.issues[0].message
        assert count(pool, "partner") == 2 and count(pool, "project") == 3
    finally:
        pool.close()
