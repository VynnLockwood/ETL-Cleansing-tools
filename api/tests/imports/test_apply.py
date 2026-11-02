from decimal import Decimal

import psycopg
import pytest
from psycopg import errors
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app import db
from app.imports.apply import convert_value
from app.imports.apply import apply as apply_import
from app.imports.models import ColumnProfile, ForeignKey, Proposal, Sheet, TableProposal, Workbook

PASSWORDS = {"spp_api": "apipw", "spp_reader": "readerpw"}


def _dsn(owner_url: str, role: str) -> str:
    params = conninfo_to_dict(owner_url)
    params["user"] = role
    params["password"] = PASSWORDS[role]
    return make_conninfo(**params)


def col(name, sql_type, unique=False):
    return ColumnProfile(
        name=name, sql_type=sql_type, non_null=0, distinct=0, unique=unique, samples=[]
    )


@pytest.fixture
def pool(clean_db):
    db.migrate(clean_db)
    p = db.open_pool(_dsn(clean_db, "spp_api"))
    yield p
    p.close()


def tables_in_spp(url):
    with psycopg.connect(_dsn(url, "spp_api")) as conn:
        return {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'spp'"
            )
        }


def count(url, table):
    with psycopg.connect(_dsn(url, "spp_api")) as conn:
        return conn.execute(f'SELECT count(*) FROM spp."{table}"').fetchone()[0]


def two_table_proposal(schema_only=False):
    return Proposal(
        tables=[
            TableProposal(name="dept", columns=[col("id", "integer"), col("name", "text")],
                          primary_key=["id"]),
            TableProposal(name="emp", columns=[col("id", "integer"), col("dept_id", "integer"),
                                               col("salary", "numeric(8,2)")],
                          primary_key=["id"]),
        ],
        foreign_keys=[ForeignKey(table="emp", column="dept_id", ref_table="dept", ref_column="id",
                                 source="name", unmatched=0, accepted=True)],
        schema_only=schema_only,
    )


def two_table_workbook(emp_rows=None):
    return Workbook(sheets=[
        Sheet("emp", ["id", "dept_id", "salary"],
              emp_rows if emp_rows is not None else [["1", "10", "100.50"], ["2", "10", ""]]),
        Sheet("dept", ["id", "name"], [["10", "Ops"], ["20", "HR"]]),
    ])


def test_creates_and_loads(pool, clean_db):
    r = apply_import(pool, two_table_proposal(), two_table_workbook())
    assert r.created == ["dept", "emp"] and r.skipped == [] and r.issues == []
    assert r.rows_loaded == {"dept": 2, "emp": 2}
    assert count(clean_db, "dept") == 2 and count(clean_db, "emp") == 2
    with psycopg.connect(_dsn(clean_db, "spp_api")) as conn:
        fk = conn.execute(
            "SELECT count(*) FROM information_schema.table_constraints "
            "WHERE table_schema='spp' AND table_name='emp' AND constraint_type='FOREIGN KEY'"
        ).fetchone()[0]
    assert fk == 1


def test_blank_cells_load_as_null(pool, clean_db):
    apply_import(pool, two_table_proposal(), two_table_workbook([["1", "10", "  "]]))
    with psycopg.connect(_dsn(clean_db, "spp_api")) as conn:
        assert conn.execute('SELECT salary FROM spp."emp"').fetchone()[0] is None


def test_rolls_back_on_bad_value(pool, clean_db):
    wb = two_table_workbook([["1", "10", "1"], ["x", "10", "1"]])
    r = apply_import(pool, two_table_proposal(), wb)
    assert r.created == []
    assert [(i.sheet, i.column, i.row) for i in r.issues] == [("emp", "id", 3)]
    assert tables_in_spp(clean_db) == set()


def test_unknown_header_is_issue(pool, clean_db):
    wb = Workbook(sheets=[
        Sheet("dept", ["id", "name", "bogus"], [["1", "a", "z"]]),
        Sheet("emp", ["id", "dept_id"], []),
    ])
    r = apply_import(pool, two_table_proposal(), wb)
    assert r.created == []
    cols = {(i.sheet, i.column) for i in r.issues}
    assert ("dept", "bogus") in cols and ("emp", "salary") in cols
    assert tables_in_spp(clean_db) == set()


def test_rolls_back_on_orphan(pool, clean_db):
    wb = two_table_workbook([["1", "99", "1"]])
    with pytest.raises(errors.ForeignKeyViolation):
        apply_import(pool, two_table_proposal(), wb)
    assert tables_in_spp(clean_db) == set()


def test_schema_only_creates_without_rows(pool, clean_db):
    r = apply_import(pool, two_table_proposal(schema_only=True), two_table_workbook())
    assert r.created == ["dept", "emp"] and r.rows_loaded == {}
    assert count(clean_db, "dept") == 0 and count(clean_db, "emp") == 0


def test_reimport_skips_existing_tables(pool, clean_db):
    apply_import(pool, two_table_proposal(), two_table_workbook())
    r = apply_import(pool, two_table_proposal(), two_table_workbook())
    assert r.created == [] and r.skipped == ["dept", "emp"] and r.issues == []
    assert count(clean_db, "dept") == 2 and count(clean_db, "emp") == 2


def test_new_table_references_skipped_table(pool, clean_db):
    apply_import(pool, two_table_proposal(), two_table_workbook())
    with psycopg.connect(_dsn(clean_db, "spp_api"), autocommit=True) as conn:
        conn.execute("DROP TABLE spp.emp")
    r = apply_import(pool, two_table_proposal(), two_table_workbook())
    assert r.created == ["emp"] and r.skipped == ["dept"]
    assert count(clean_db, "dept") == 2 and count(clean_db, "emp") == 2


def test_reader_can_select_created_table(pool, clean_db):
    apply_import(pool, two_table_proposal(), two_table_workbook())
    with psycopg.connect(_dsn(clean_db, "spp_reader")) as conn:
        assert conn.execute('SELECT count(*) FROM spp."dept"').fetchone()[0] == 2


@pytest.mark.parametrize(
    "sql_type,text,expected",
    [
        ("integer", "  ", None),
        ("text", "", None),
        ("boolean", "TRUE", True),
        ("boolean", "false", False),
        ("smallint", "32767", 32767),
        ("integer", "-5", -5),
        ("bigint", "9223372036854775807", 9223372036854775807),
        ("numeric(5,2)", "123.45", Decimal("123.45")),
        ("numeric(5,2)", "7", Decimal("7")),
        ("numeric(5,2)", "-0.5", Decimal("-0.5")),
        ("numeric(5,2)", "999.99", Decimal("999.99")),
        ("numeric(5,2)", "-999.99", Decimal("-999.99")),
        ("numeric(5,2)", "1.50", Decimal("1.50")),
        ("numeric(5,2)", "1.500", Decimal("1.500")),
        ("numeric(5,2)", "0", Decimal("0")),
        ("numeric(5)", "99999", Decimal("99999")),
        ("numeric(30,0)", "1" * 30, Decimal("1" * 30)),
        ("numeric(29,9)", "12345678901234567890.123456789",
         Decimal("12345678901234567890.123456789")),
        ("numeric(38,10)", "1" * 19, Decimal("1" * 19)),
        ("timestamp", "2024-01-02", __import__("datetime").datetime(2024, 1, 2)),
        ("timestamptz", "2024-01-02", __import__("datetime").datetime(2024, 1, 2)),
        ("double precision", "1.5", 1.5),
        ("date", "2024-02-29", __import__("datetime").date(2024, 2, 29)),
        ("timestamp", "2024-01-02T03:04:05", __import__("datetime").datetime(2024, 1, 2, 3, 4, 5)),
        ("timestamptz", "2024-01-02 03:04:05", __import__("datetime").datetime(2024, 1, 2, 3, 4, 5)),
        ("uuid", "12345678-1234-5678-1234-567812345678",
         __import__("uuid").UUID("12345678-1234-5678-1234-567812345678")),
        ("varchar(3)", "abc", "abc"),
        ("text", " keep ", " keep "),
    ],
)
def test_convert_value_ok(sql_type, text, expected):
    assert convert_value(sql_type, text) == expected


@pytest.mark.parametrize(
    "sql_type,text",
    [
        ("integer", "abc"),
        ("integer", "1.5"),
        ("integer", "๑๒๓"),
        ("smallint", "32768"),
        ("integer", "2147483648"),
        ("bigint", "9223372036854775808"),
        ("numeric(5,2)", "1234.5"),
        ("numeric(5,2)", "1.234"),
        ("numeric(5,2)", "1000.00"),
        ("numeric(5,2)", "1234567"),
        ("numeric(5,2)", "-1000"),
        ("numeric(30,0)", "1" * 31),
        ("numeric(38,10)", "1" * 29),
        ("numeric(5,2)", "abc"),
        ("numeric(5,2)", "NaN"),
        ("double precision", "x"),
        ("double precision", "nan"),
        ("boolean", "yes"),
        ("date", "2024-02-30"),
        ("date", "01/02/2024"),
        ("timestamp", "2024-01-02T03:04"),
        ("timestamptz", "2024-13-02"),
        ("timestamp", "๒๐๒๔-๐๑-๐๒"),
        ("uuid", "not-a-uuid"),
        ("varchar(3)", "abcd"),
        ("text", "a\x00b"),
    ],
)
def test_convert_value_rejects(sql_type, text):
    with pytest.raises(ValueError):
        convert_value(sql_type, text)


def test_wide_numeric_and_midnight_timestamp_load(pool, clean_db):
    p = Proposal(tables=[TableProposal(
        name="w", columns=[col("n", "numeric(38,10)"), col("t", "timestamp")], primary_key=[])],
        foreign_keys=[])
    wb = Workbook(sheets=[Sheet("w", ["n", "t"], [["1" * 19 + ".1234567890", "2024-01-02"]])])
    r = apply_import(pool, p, wb)
    assert r.issues == [] and r.rows_loaded == {"w": 1}


def test_convert_unbounded_varchar_numeric_jsonb():
    thai = "สวัสดี" * 100
    assert convert_value("varchar", thai) == thai
    assert convert_value("varchar", " keep ") == " keep "
    assert convert_value("varchar", "") is None
    with pytest.raises(ValueError):
        convert_value("varchar", "a\x00b")

    assert convert_value("numeric", "12.50") == Decimal("12.50")
    assert convert_value("numeric", " -3 ") == Decimal("-3")
    assert convert_value("numeric", "1" * 1000) == Decimal("1" * 1000)
    assert convert_value("numeric", "") is None
    for bad in ("1" * 1001, "1." + "1" * 1000, "abc", "NaN", "Infinity", "1e5", "๑๒๓"):
        with pytest.raises(ValueError):
            convert_value("numeric", bad)

    assert convert_value("jsonb", '{"a": 1}') == '{"a": 1}'
    assert convert_value("jsonb", " [1, 2] ") == " [1, 2] "
    assert convert_value("jsonb", "  ") is None
    for bad in ("{bad", "NaN", "a\x00b", '"\\u0000"'):
        with pytest.raises(ValueError):
            convert_value("jsonb", bad)
    with pytest.raises(ValueError, match="not valid JSON"):
        convert_value("jsonb", "{bad")


def test_unbounded_types_load_through_apply(pool, clean_db):
    p = Proposal(tables=[TableProposal(
        name="u", columns=[col("v", "varchar"), col("n", "numeric"), col("j", "jsonb")],
        primary_key=[])], foreign_keys=[])
    wb = Workbook(sheets=[Sheet("u", ["v", "n", "j"], [["สวัสดี", "1.25", '{"a": 1}']])])
    r = apply_import(pool, p, wb)
    assert r.issues == [] and r.rows_loaded == {"u": 1}
    with pool.connection() as conn:
        assert conn.execute("SELECT v, n, j FROM spp.u").fetchone() == (
            "สวัสดี", Decimal("1.25"), {"a": 1})


def _real_dictionary():
    from pathlib import Path
    from app.imports.dictionary import parse_dictionary
    from app.imports.workbook import read_workbook
    path = Path(__file__).resolve().parents[3] / "docs" / "data-dict-spp.xlsx"
    if not path.exists():
        pytest.skip("real dictionary not available")
    with path.open("rb") as f:
        wb, _ = read_workbook(f)
    return parse_dictionary(wb)[0]


def test_real_dictionary_applies_cleanly(clean_db):
    p = _real_dictionary()
    assert len(p.tables) == 58
    names = {t.name for t in p.tables}
    expected_fks = sum(1 for f in p.foreign_keys
                       if f.accepted and f.table in names and f.ref_table in names)
    assert expected_fks > 0
    db.migrate(clean_db)
    pool = db.open_pool(clean_db)
    try:
        r = apply_import(pool, p, Workbook(sheets=[]))
    finally:
        pool.close()
    assert r.issues == []
    assert sorted(r.created) == sorted(names)
    with psycopg.connect(clean_db) as conn:
        n_tables = conn.execute("SELECT count(*) FROM pg_catalog.pg_tables "
                                "WHERE schemaname = 'spp'").fetchone()[0]
        n_fks = conn.execute(
            "SELECT count(*) FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_namespace n "
            "ON n.oid = k.connamespace WHERE k.contype = 'f' AND n.nspname = 'spp'").fetchone()[0]
    assert n_tables == 58
    assert n_fks == expected_fks
