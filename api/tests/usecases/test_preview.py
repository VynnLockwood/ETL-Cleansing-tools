import json

import psycopg
import pytest
from psycopg import sql

from app import db
from app.usecases import preview
from app.usecases.graph import load_graph
from app.usecases.planner import plan_queries
from app.usecases.preview import read_only_connection, run_preview
from app.usecases.resolver import resolve
from conftest import role_dsn
from uc_helpers import F, make_small_data


@pytest.fixture
def env(clean_db):
    make_small_data(clean_db)
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute(
            "CREATE TABLE spp.types (id int PRIMARY KEY, n numeric, u uuid, d date,"
            " ts timestamptz, bb bytea, j jsonb, iv interval, f double precision);"
            "INSERT INTO spp.types VALUES (1, 12.50, '00000000-0000-0000-0000-000000000001',"
            " '2025-03-01', '2025-03-01 10:30:00+00', '\\xdeadbeef', '{\"a\": [1, 2]}',"
            " '1 day', 1.5);"
            "CREATE TABLE spp.empty_t (x int);"
        )
        graph = load_graph(c)
    pool = db.open_pool(role_dsn(clean_db, "spp_reader"))
    yield clean_db, graph, pool
    pool.close()


def specs(graph, fields, start=None):
    return plan_queries(graph, resolve(graph, fields, start))


def test_rows_and_columns(env):
    _, g, pool = env
    [r] = run_preview(pool, g, specs(g, [F("b", "label")]), 50, 5000)
    assert (r.id, r.kind, r.title, r.error, r.truncated) == ("main", "main", "b", None, False)
    assert r.columns == ["b.label", "b.id"]
    assert sorted(r.rows) == [["b-10", 10], ["b-11", 11], ["b-12", 12]]
    assert r.sql.startswith("SELECT") and r.sql.endswith("LIMIT 50")
    assert r.merge_key == []


def test_linked_result_has_merge_key(env):
    _, g, pool = env
    results = run_preview(pool, g, specs(g, [F("a", "name"), F("b", "label")], "a"), 50, 5000)
    assert [r.id for r in results] == ["main", "linked-1"]
    assert results[1].kind == "linked" and results[1].merge_key == ["b.a_id"]


def test_truncated_flag_exact(env):
    _, g, pool = env
    sp = specs(g, [F("b", "label")])
    [r] = run_preview(pool, g, sp, 3, 5000)
    assert len(r.rows) == 3 and r.truncated is False
    [r] = run_preview(pool, g, sp, 2, 5000)
    assert len(r.rows) == 2 and r.truncated is True
    assert r.sql.endswith("LIMIT 2")


def test_json_safe_values(env):
    _, g, pool = env
    fields = [F("types", c) for c in ("n", "u", "d", "ts", "bb", "j", "iv", "f")]
    [r] = run_preview(pool, g, specs(g, fields), 50, 5000)
    assert r.error is None
    json.dumps(r.rows)  # must not raise
    row = dict(zip(r.columns, r.rows[0]))
    assert row["types.n"] == "12.50"
    assert row["types.u"] == "00000000-0000-0000-0000-000000000001"
    assert row["types.d"] == "2025-03-01"
    assert row["types.ts"].startswith("2025-03-01T10:30:00")
    assert row["types.bb"] == "deadbeef"
    assert row["types.j"] == {"a": [1, 2]}
    assert row["types.f"] == 1.5


def test_empty_table_returns_no_rows_no_error(env):
    _, g, pool = env
    [r] = run_preview(pool, g, specs(g, [F("empty_t", "x")]), 50, 5000)
    assert r.rows == [] and r.error is None and r.truncated is False


def test_timeout_gives_error_for_that_query_only(env, monkeypatch):
    _, g, pool = env
    sp = specs(g, [F("a", "name"), F("b", "label")], "a")
    real = preview.compose

    def fake(spec, graph, limit):
        if spec.id == "main":
            return sql.SQL("SELECT pg_sleep(2)")
        return real(spec, graph, limit)

    monkeypatch.setattr(preview, "compose", fake)
    main, linked = run_preview(pool, g, sp, 50, 200)
    assert main.rows == [] and "timeout" in main.error
    assert linked.error is None and len(linked.rows) == 3


def test_connection_is_read_only_with_timeout(env):
    _, _, pool = env
    with read_only_connection(pool, 1234) as conn:
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "1234ms"


def test_permission_denied_becomes_error(env):
    url, g, pool = env
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("REVOKE SELECT ON spp.a FROM spp_reader")
    sp = specs(g, [F("a", "name")]) + specs(g, [F("b", "label")])
    denied, fine = run_preview(pool, g, sp, 50, 5000)
    assert "permission denied" in denied.error and denied.rows == []
    assert fine.error is None and len(fine.rows) == 3


def test_long_column_names_are_not_truncated_or_merged(env):
    url, _, pool = env
    table = "t" * 45
    col1 = "c" * 25 + "_first"
    col2 = "c" * 25 + "_second"  # "table.column" is over 63 bytes and the first 63 bytes are equal
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f'CREATE TABLE spp."{table}" (id int PRIMARY KEY, "{col1}" text, "{col2}" text)')
        c.execute(f"INSERT INTO spp.\"{table}\" VALUES (1, 'one', 'two')")
        g = load_graph(c)
    assert len(f"{table}.{col1}".encode()) > 63
    [r] = run_preview(pool, g, specs(g, [F(table, col1), F(table, col2)]), 50, 5000)
    assert r.error is None
    assert r.columns == [f"{table}.{col1}", f"{table}.{col2}", f"{table}.id"]
    assert r.rows == [["one", "two", 1]]
