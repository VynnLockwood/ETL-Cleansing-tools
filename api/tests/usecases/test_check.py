import psycopg
import pytest

from app import db
from app.usecases.check import run_check
from conftest import role_dsn
from uc_helpers import F, load_real_schema, make_config, make_small_data, make_use_case


@pytest.fixture
def pool(clean_db):
    p = db.open_pool(role_dsn(clean_db, "spp_reader"))
    yield p
    p.close()


def check(pool, url, uc, tmp_path):
    return run_check(pool, make_config(url, tmp_path), uc)


def test_check_ok_unreachable_and_missing_fields(clean_db, pool, tmp_path):
    make_small_data(clean_db)
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("CREATE TABLE spp.gone (id int PRIMARY KEY, v text)")
    uc = make_use_case([F("a", "name"), F("b", "label"), F("gone", "v"), F("nokey", "v"), F("a", "nope")], "a")
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("DROP TABLE spp.gone")  # dropped after the use case was saved
    r = check(pool, clean_db, uc, tmp_path)
    status = {(f.table, f.column): f for f in r.fields}
    assert status[("a", "name")].status == "ok" and status[("a", "name")].query == "main"
    assert status[("b", "label")].status == "ok" and status[("b", "label")].query == "linked-1"
    assert status[("b", "label")].via == ["b.a_id -> a"]
    assert status[("gone", "v")].status == "missing"
    assert status[("gone", "v")].reason == "table or column no longer exists"
    assert status[("a", "nope")].status == "missing"
    assert status[("nokey", "v")].status == "unreachable"
    assert status[("nokey", "v")].reason == "no foreign key path from a"
    assert r.start_table == "a" and r.start_table_reason.startswith("chosen by you")


def test_check_returns_queries_with_rows(clean_db, pool, tmp_path):
    make_small_data(clean_db)
    r = check(pool, clean_db, make_use_case([F("a", "name"), F("b", "label")], "a"), tmp_path)
    assert [q.id for q in r.queries] == ["main", "linked-1"]
    assert sorted(r.queries[0].rows) == [["A1", 1], ["A2", 2]]
    assert len(r.queries[1].rows) == 3 and all(q.error is None for q in r.queries)
    assert {n.table for n in r.erd.nodes} == {"a", "b"}
    assert [x.code for x in r.recommendations] == ["FAN_OUT"]


def test_check_survives_failing_query(clean_db, pool, tmp_path):
    make_small_data(clean_db)
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("REVOKE SELECT ON spp.b FROM spp_reader")
    r = check(pool, clean_db, make_use_case([F("a", "name"), F("b", "label")], "a"), tmp_path)
    main, linked = r.queries
    assert main.error is None and len(main.rows) == 2
    assert "permission denied" in linked.error


def test_check_with_no_fields_resolves_nothing(clean_db, pool, tmp_path):
    make_small_data(clean_db)
    r = check(pool, clean_db, make_use_case([F("ghost", "x")]), tmp_path)
    assert r.start_table == "" and r.queries == [] and r.erd.nodes == []
    assert r.fields[0].status == "missing"


def test_real_schema_success_criterion(clean_db, pool, tmp_path):
    load_real_schema(clean_db)
    import uuid

    partner, project, conn_id = (str(uuid.uuid4()) for _ in range(3))
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute(
            'INSERT INTO spp."sppBusinessPartner" ("businessPartnerID", "juristicName") VALUES (%s, %s)',
            (partner, "Acme Co"),
        )
        c.execute(
            'INSERT INTO spp."sppProject" ("sppProjectID", "sppBusinessPartner", "projectCode") VALUES (%s, %s, %s)',
            (project, partner, "P-001"),
        )
        c.execute(
            'INSERT INTO spp."sppProjectConnection" ("sppProjectConnectionID", "sppProject", "egatMainSubstation")'
            " VALUES (%s, %s, %s)",
            (conn_id, project, "Sub-1"),
        )
    fields = [
        F("sppProject", "projectCode"),
        F("sppBusinessPartner", "juristicName"),
        F("sppProjectConnection", "egatMainSubstation"),
        F("sppProjectAddress", "fullAddress"),
    ]
    r = check(pool, clean_db, make_use_case(fields, "sppProject"), tmp_path)
    st = {f.table: f for f in r.fields}
    assert st["sppProject"].status == "ok" and st["sppProject"].query == "main"
    assert st["sppBusinessPartner"].status == "ok" and st["sppBusinessPartner"].query == "main"
    assert st["sppProjectConnection"].status == "ok" and st["sppProjectConnection"].query == "linked-1"
    assert st["sppProjectAddress"].status == "unreachable"
    assert all(q.error is None for q in r.queries)
    assert r.queries[0].rows == [["P-001", "Acme Co", project]]
    assert r.queries[1].rows == [["Sub-1", project]]
    rec = next(x for x in r.recommendations if x.code == "NO_PATH_KEY_MATCH")
    assert (rec.fix.table, rec.fix.column, rec.fix.ref_table, rec.fix.ref_column) == (
        "sppProjectAddress", "sppProject", "sppProject", "sppProjectID",
    )
    assert any(e.status == "suggested" and e.from_ == "sppProjectAddress" for e in r.erd.edges)
    assert any(x.code == "FAN_OUT" for x in r.recommendations)


def test_real_schema_automatic_start_is_the_flat_one(clean_db, pool, tmp_path):
    load_real_schema(clean_db)
    fields = [
        F("sppProject", "projectCode"),
        F("sppBusinessPartner", "juristicName"),
        F("sppProjectConnection", "egatMainSubstation"),
    ]
    r = check(pool, clean_db, make_use_case(fields), tmp_path)
    assert r.start_table == "sppProjectConnection"
    assert [q.id for q in r.queries] == ["main"]
