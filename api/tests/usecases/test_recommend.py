import psycopg
import pytest

from app import db
from app.usecases.graph import load_graph
from app.usecases.models import Evidence, Fix
from app.usecases.planner import plan_queries
from app.usecases.recommend import recommend
from app.usecases.resolver import resolve
from conftest import role_dsn
from uc_helpers import F, load_real_schema


class Env:
    def __init__(self, url, sql_text, fields, start=None):
        db.migrate(url)
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(sql_text)
            self.graph = load_graph(c)
        self.res = resolve(self.graph, fields, start)
        self.specs = plan_queries(self.graph, self.res)
        self.pool = db.open_pool(role_dsn(url, "spp_reader"))

    def recs(self):
        return recommend(self.pool, self.graph, self.res, self.specs, 5000)

    def close(self):
        self.pool.close()


@pytest.fixture
def make(clean_db):
    envs = []

    def _make(sql_text, fields, start=None):
        e = Env(clean_db, sql_text, fields, start)
        envs.append(e)
        return e

    yield _make
    for e in envs:
        e.close()


def codes(recs):
    return [r.code for r in recs]


def by_code(recs, code):
    return [r for r in recs if r.code == code]


PROJ_ADDR = """
CREATE TABLE spp.proj (id int PRIMARY KEY, code text);
CREATE TABLE spp.addr (id int PRIMARY KEY, proj_ref int, other int, city text);
INSERT INTO spp.proj VALUES (1, 'P1'), (2, 'P2');
"""
FIELDS = [F("proj", "code"), F("addr", "city")]


def test_key_match_suggests_fk_with_evidence(make):
    e = make(PROJ_ADDR + "INSERT INTO spp.addr VALUES (1, 1, 1, 'x'), (2, 2, 2, 'y'), (3, 1, 2, 'z');", FIELDS)
    assert e.res.start == "addr"  # proj is the unreachable one
    [r] = by_code(e.recs(), "NO_PATH_KEY_MATCH")
    assert r.severity == "error" and r.table == "proj"
    assert r.fix == Fix(table="addr", column="proj_ref", ref_table="proj", ref_column="id")
    assert r.evidence == Evidence(matched=3, total=3)
    assert "3 of 3" in r.message and r.field == F("proj", "code")


def test_key_match_requires_all_values_contained(make):
    e = make(PROJ_ADDR + "INSERT INTO spp.addr VALUES (1, 1, 1, 'x'), (2, 2, 1, 'y'), (3, 99, 1, 'z');", FIELDS)
    # other (1,1,1) fits but its name has nothing to do with proj: still a valid candidate by data
    recs = e.recs()
    [r] = by_code(recs, "NO_PATH_KEY_MATCH")
    assert r.fix.column == "other"  # proj_ref has an orphan (99), so only `other` qualifies
    assert r.evidence == Evidence(matched=3, total=3)


def test_no_candidate_gives_isolated_table(make):
    e = make(PROJ_ADDR + "INSERT INTO spp.addr VALUES (1, 99, 98, 'x');", FIELDS)
    recs = e.recs()
    assert "NO_PATH_KEY_MATCH" not in codes(recs)
    [r] = by_code(recs, "ISOLATED_TABLE")
    assert r.severity == "error" and r.table == "proj"


def test_key_match_empty_table_uses_name(make):
    e = make(PROJ_ADDR, FIELDS)
    [r] = by_code(e.recs(), "NO_PATH_KEY_MATCH")
    assert r.fix.column == "proj_ref"  # name contains "proj"; `other` does not
    assert r.evidence == Evidence(matched=0, total=0)
    assert "No data to confirm" in r.message


def test_key_match_empty_table_without_name_hint_is_isolated(make):
    sql = PROJ_ADDR.replace("proj_ref", "ref").replace("other", "x2")
    e = make(sql, FIELDS)
    recs = e.recs()
    assert "NO_PATH_KEY_MATCH" not in codes(recs) and "ISOLATED_TABLE" in codes(recs)


def test_key_match_picks_best_candidate_per_table(make):
    e = make(PROJ_ADDR + "INSERT INTO spp.addr VALUES (1, 1, 1, 'x'), (2, 2, 2, 'y');", FIELDS)
    recs = by_code(e.recs(), "NO_PATH_KEY_MATCH")
    assert len(recs) == 1 and recs[0].fix.column == "proj_ref"  # name hint beats plain data match


def test_key_match_only_same_type_family(make):
    sql = """
CREATE TABLE spp.proj (id int PRIMARY KEY, code text);
CREATE TABLE spp.addr (id int PRIMARY KEY, proj_ref text, city text);
INSERT INTO spp.proj VALUES (1, 'P1');
INSERT INTO spp.addr VALUES (1, '1', 'x');
"""
    e = make(sql, FIELDS)
    recs = e.recs()
    assert "NO_PATH_KEY_MATCH" not in codes(recs) and "ISOLATED_TABLE" in codes(recs)


def test_key_match_notes_type_difference_in_same_family(make):
    sql = """
CREATE TABLE spp.proj (id text PRIMARY KEY, code text);
CREATE TABLE spp.addr (id int PRIMARY KEY, proj_ref varchar(10), city text);
INSERT INTO spp.proj VALUES ('1', 'P1');
INSERT INTO spp.addr VALUES (1, '1', 'x');
"""
    e = make(sql, FIELDS)
    [r] = by_code(e.recs(), "NO_PATH_KEY_MATCH")
    assert "Align the column types" in r.message
    assert r.evidence == Evidence(matched=1, total=1)


HOST = """
CREATE TABLE spp.mtok (id int PRIMARY KEY);
CREATE TABLE spp.mtlinked (id int PRIMARY KEY);
CREATE TABLE spp."mtOk" (id int PRIMARY KEY);
CREATE TABLE spp.host (id int PRIMARY KEY, "mtZone" text, "mtOk" text, "mtLinked" int REFERENCES spp.mtlinked (id), mtlower text);
"""


def test_missing_table_for_mt_column(make):
    e = make(HOST, [F("host", "id")])
    [r] = by_code(e.recs(), "MISSING_TABLE")
    assert r.severity == "warning" and r.table == "host"
    assert "'mtZone'" in r.message and "host.mtZone" in r.message


def test_mt_column_with_fk_or_table_is_quiet(make):
    e = make(HOST, [F("host", "id")])
    msgs = " ".join(r.message for r in by_code(e.recs(), "MISSING_TABLE"))
    assert "mtOk" not in msgs and "mtLinked" not in msgs and "mtlower" not in msgs


def test_missing_table_only_for_tables_the_use_case_reads(make):
    e = make(HOST + "CREATE TABLE spp.other (id int PRIMARY KEY, v text);", [F("other", "v")])
    assert by_code(e.recs(), "MISSING_TABLE") == []


def test_no_primary_key(make):
    sql = PROJ_ADDR + "CREATE TABLE spp.nopk (v text);"
    e = make(sql, [F("proj", "code"), F("nopk", "v")], start="proj")
    recs = e.recs()
    [r] = by_code(recs, "NO_PRIMARY_KEY")
    assert r.severity == "error" and r.table == "nopk"
    [iso] = by_code(recs, "ISOLATED_TABLE")
    assert iso.table == "nopk"


def test_ambiguous_path_lists_alternatives(make):
    sql = """
CREATE TABLE spp.a (id int PRIMARY KEY, x text);
CREATE TABLE spp.b (id int PRIMARY KEY, a1 int REFERENCES spp.a (id), a2 int REFERENCES spp.a (id), y text);
"""
    e = make(sql, [F("a", "x"), F("b", "y")])
    [r] = by_code(e.recs(), "AMBIGUOUS_PATH")
    assert r.severity == "warning" and r.table == "a"
    assert "b.a1 -> a" in r.message and "b.a2 -> a" in r.message


def test_isolated_not_reported_when_table_has_edges(make):
    sql = PROJ_ADDR + """
CREATE TABLE spp.z1 (id int PRIMARY KEY);
CREATE TABLE spp.z2 (id int PRIMARY KEY, z1_id int REFERENCES spp.z1 (id), v text);
"""
    e = make(sql, [F("proj", "code"), F("z2", "v")], start="proj")
    assert by_code(e.recs(), "ISOLATED_TABLE") == []


def test_fan_out_info_per_linked_query(make):
    sql = """
CREATE TABLE spp.p (id int PRIMARY KEY, n text);
CREATE TABLE spp.c (id int PRIMARY KEY, p_id int REFERENCES spp.p (id), v text);
"""
    e = make(sql, [F("p", "n"), F("c", "v")], start="p")
    [r] = by_code(e.recs(), "FAN_OUT")
    assert r.severity == "info" and r.table == "c" and "c per p" in r.message


def test_ordering_by_severity_then_code_then_table(make):
    sql = HOST + "CREATE TABLE spp.p (id int PRIMARY KEY, n text);" \
        "CREATE TABLE spp.c (id int PRIMARY KEY, host_id int REFERENCES spp.host (id), v text);" \
        "CREATE TABLE spp.nopk (v text);"
    e = make(sql, [F("host", "id"), F("c", "v"), F("nopk", "v")], start="host")
    recs = e.recs()
    rank = {"error": 0, "warning": 1, "info": 2}
    keys = [(rank[r.severity], r.code, r.table or "") for r in recs]
    assert keys == sorted(keys) and len(recs) >= 4


def test_real_dictionary_address_suggestion(clean_db):
    load_real_schema(clean_db)
    with psycopg.connect(clean_db) as c:
        g = load_graph(c)
    res = resolve(g, [F("sppProject", "projectCode"), F("sppProjectAddress", "fullAddress")])
    assert res.start == "sppProject"
    pool = db.open_pool(role_dsn(clean_db, "spp_reader"))
    try:
        recs = recommend(pool, g, res, plan_queries(g, res), 5000)
    finally:
        pool.close()
    [r] = by_code(recs, "NO_PATH_KEY_MATCH")
    assert r.fix == Fix(
        table="sppProjectAddress", column="sppProject", ref_table="sppProject", ref_column="sppProjectID"
    )
    assert r.evidence == Evidence(matched=0, total=0)
    assert any("mtZone" in m.message for m in by_code(recs, "MISSING_TABLE"))


# ---- final review fixes ---------------------------------------------------------------


def test_timed_out_evidence_is_reported_not_ignored(make, monkeypatch):
    from app.usecases import recommend as rec_mod

    e = make(PROJ_ADDR + "INSERT INTO spp.addr VALUES (1, 1, 1, 'x');", FIELDS)
    monkeypatch.setattr(rec_mod, "_count", lambda *a, **k: None)  # every evidence query fails
    recs = e.recs()
    assert "ISOLATED_TABLE" not in codes(recs)
    [r] = by_code(recs, "NO_PATH_KEY_MATCH")
    assert r.severity == "warning" and r.fix is None and "time limit" in r.message


def test_partial_timeout_is_mentioned_on_the_suggestion(make, monkeypatch):
    from app.usecases import recommend as rec_mod

    e = make(PROJ_ADDR + "INSERT INTO spp.addr VALUES (1, 1, 1, 'x'), (2, 2, 2, 'y');", FIELDS)
    real = rec_mod._count
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*a, **k)

    monkeypatch.setattr(rec_mod, "_count", flaky)
    [r] = by_code(e.recs(), "NO_PATH_KEY_MATCH")
    assert r.severity == "error" and r.fix is not None
    assert "time limit" in r.message


def test_evidence_only_for_tables_the_use_case_uses(make, monkeypatch):
    from app.usecases import recommend as rec_mod

    sql = PROJ_ADDR + """
CREATE TABLE spp.ext (id int PRIMARY KEY, proj_id int REFERENCES spp.addr (id), v int);
INSERT INTO spp.addr VALUES (1, 1, 1, 'x');
"""
    e = make(sql, FIELDS, start="addr")  # ext is connected to addr but the use case never reads it
    assert "ext" in e.res.paths
    seen = []
    real = rec_mod._count
    monkeypatch.setattr(rec_mod, "_count", lambda pool, t, holder, hc, key, kc, same: (
        seen.append((holder, key)), real(pool, t, holder, hc, key, kc, same))[1])
    e.recs()
    assert seen and all("ext" not in pair for pair in seen)


def test_foreign_key_columns_are_not_offered_as_new_keys(make):
    sql = """
CREATE TABLE spp.proj (id int PRIMARY KEY, code text);
CREATE TABLE spp.other (id int PRIMARY KEY);
CREATE TABLE spp.addr (id int PRIMARY KEY, proj_ref int REFERENCES spp.other (id), city text);
INSERT INTO spp.proj VALUES (1, 'P1'), (2, 'P2');
INSERT INTO spp.other VALUES (1), (2);
INSERT INTO spp.addr VALUES (1, 1, 'x'), (2, 2, 'y');
"""
    e = make(sql, FIELDS)
    recs = e.recs()
    assert "NO_PATH_KEY_MATCH" not in codes(recs) and "ISOLATED_TABLE" in codes(recs)


def test_missing_table_ignores_lookup_tables_own_columns(make):
    sql = 'CREATE TABLE spp."mtFuel" ("mtFuelID" text PRIMARY KEY, "mtFuelName" text, "mtFuelCode" text);'
    e = make(sql, [F("mtFuel", "mtFuelName")])
    assert by_code(e.recs(), "MISSING_TABLE") == []


def test_missing_table_ignores_primary_key_columns(make):
    e = make('CREATE TABLE spp.other2 ("mtKey" int PRIMARY KEY, v text);', [F("other2", "v")])
    assert by_code(e.recs(), "MISSING_TABLE") == []


def test_no_primary_key_not_reported_when_that_table_holds_the_key(make):
    sql = """
CREATE TABLE spp.proj (id int PRIMARY KEY, code text);
CREATE TABLE spp.addr_nopk (proj_ref int, city text);
INSERT INTO spp.proj VALUES (1, 'P1'), (2, 'P2');
INSERT INTO spp.addr_nopk VALUES (1, 'x'), (2, 'y');
"""
    e = make(sql, [F("proj", "code"), F("addr_nopk", "city")], start="proj")
    recs = e.recs()
    [r] = by_code(recs, "NO_PATH_KEY_MATCH")
    assert r.fix.table == "addr_nopk"
    assert "NO_PRIMARY_KEY" not in codes(recs)
