import psycopg
import pytest

from app.usecases.graph import load_graph
from app.usecases.planner import SelectItem, compose, plan_queries
from app.usecases.resolver import resolve
from uc_helpers import F, make_small_data, mk_graph

# p <- c (c.p_id -> p) ; c -> q (c.q_id -> q) ; p <- u one-to-one (u.p_id unique -> p)
G = mk_graph(
    {
        "p": ["id", "name"],
        "c": ["id", "p_id", "q_id", "note"],
        "q": ["id", "qname"],
        "u": ["id", "p_id", "extra"],
        "n": ["v"],
    },
    [
        ("c_p", "c", ["p_id"], "p", ["id"], False),
        ("c_q", "c", ["q_id"], "q", ["id"], False),
        ("u_p", "u", ["p_id"], "p", ["id"], True),
    ],
    pks={"p": ["id"], "c": ["id"], "q": ["id"], "u": ["id"]},
)


def plan(graph, fields, start=None):
    return plan_queries(graph, resolve(graph, fields, start))


def aliases(spec):
    return [s.alias for s in spec.select]


def test_one_to_many_becomes_linked_query_with_merge_key():
    qs = plan(G, [F("p", "name"), F("c", "note")], start="p")
    main, linked = qs
    assert (main.id, main.kind, main.root) == ("main", "main", "p")
    assert aliases(main) == ["p.name", "p.id"]
    assert main.joins == () and main.merge == () and main.parent_query is None
    assert (linked.id, linked.kind, linked.root) == ("linked-1", "linked", "c")
    assert aliases(linked) == ["c.note", "c.p_id"]
    assert linked.merge == (("p_id", "id"),)
    assert linked.parent_query == "main"
    assert linked.title == "c per p"


def test_many_to_one_chain_stays_in_main():
    qs = plan(G, [F("c", "note"), F("p", "name"), F("q", "qname")])
    assert [q.id for q in qs] == ["main"]
    main = qs[0]
    assert main.root == "c"
    assert [(j.from_table, j.to_table) for j in main.joins] == [("c", "p"), ("c", "q")]
    assert aliases(main) == ["c.note", "p.name", "q.qname", "c.id"]


def test_second_fan_out_inside_branch_starts_another_linked_query():
    g = mk_graph(
        {"a": ["id", "x"], "b": ["id", "a_id", "y"], "c": ["id", "b_id", "z"]},
        [("b_a", "b", ["a_id"], "a", ["id"], False), ("c_b", "c", ["b_id"], "b", ["id"], False)],
        pks={"a": ["id"], "b": ["id"], "c": ["id"]},
    )
    qs = plan(g, [F("a", "x"), F("b", "y"), F("c", "z")], start="a")
    assert [(q.id, q.root, q.parent_query) for q in qs] == [
        ("main", "a", None),
        ("linked-1", "b", "main"),
        ("linked-2", "c", "linked-1"),
    ]
    assert qs[2].merge == (("b_id", "id"),)


def test_tables_sharing_a_fan_out_share_one_query():
    qs = plan(G, [F("c", "note"), F("q", "qname")], start="p")
    assert [q.id for q in qs] == ["main", "linked-1"]
    linked = qs[1]
    assert linked.root == "c"
    assert [(j.from_table, j.to_table) for j in linked.joins] == [("c", "q")]
    assert aliases(linked) == ["c.note", "q.qname", "c.p_id"]


def test_one_to_one_down_stays_in_same_query():
    qs = plan(G, [F("p", "name"), F("u", "extra")], start="p")
    assert [q.id for q in qs] == ["main"]
    assert [(j.from_table, j.to_table) for j in qs[0].joins] == [("p", "u")]


def test_main_selects_start_pk_once():
    qs = plan(G, [F("p", "id"), F("p", "name")])
    assert aliases(qs[0]) == ["p.id", "p.name"]


def test_start_without_primary_key_works():
    qs = plan(G, [F("n", "v")])
    assert [q.id for q in qs] == ["main"]
    assert aliases(qs[0]) == ["n.v"]


def test_unreachable_and_missing_fields_not_selected():
    qs = plan(G, [F("p", "name"), F("n", "v"), F("ghost", "x")], start="p")
    assert aliases(qs[0]) == ["p.name", "p.id"]


def test_main_omitted_when_it_would_select_nothing():
    qs = plan(G, [F("c", "note")], start="n")
    assert qs == []
    qs = plan(mk_graph({"t": ["v"]}, []), [])
    assert qs == []


# ---- SQL composition on a database -------------------------------------------------


@pytest.fixture
def conn(clean_db):
    make_small_data(clean_db)
    with psycopg.connect(clean_db, autocommit=True) as c:
        yield c


def run(conn, spec, graph, limit=50):
    cur = conn.execute(compose(spec, graph, limit))
    return [d.name for d in cur.description], cur.fetchall()


def graph_of(conn):
    return load_graph(conn)


def test_compose_runs_and_returns_expected_rows(conn):
    g = graph_of(conn)
    qs = plan_queries(g, resolve(g, [F("a", "name"), F("b", "label")], "a"))
    main, linked = qs
    cols, rows = run(conn, main, g)
    assert cols == ["a.name", "a.id"]
    assert sorted(rows) == [("A1", 1), ("A2", 2)]
    cols, rows = run(conn, linked, g)
    assert cols == ["b.label", "b.a_id"]
    assert sorted(rows) == [("b-10", 1), ("b-11", 1), ("b-12", 2)]
    text = compose(main, g, 50).as_string(conn)
    assert "LIMIT 50" in text and 'FROM "spp"."a" AS "t0"' in text


def test_compose_joins_parent_values(conn):
    g = graph_of(conn)
    qs = plan_queries(g, resolve(g, [F("b", "label"), F("a", "name")]))
    assert [q.id for q in qs] == ["main"]
    cols, rows = run(conn, qs[0], g)
    assert cols == ["b.label", "a.name", "b.id"]
    assert sorted(rows) == [("b-10", "A1", 10), ("b-11", "A1", 11), ("b-12", "A2", 12)]


def test_compose_quotes_odd_identifiers(conn):
    g = graph_of(conn)
    qs = plan_queries(g, resolve(g, [F('we"ird', 'co"l'), F("a", "name")]))
    cols, rows = run(conn, qs[0], g)
    assert cols[:2] == ['we"ird.co"l', "a.name"]
    assert rows == [("cv", "A1", 1)]
    cols, rows = run(conn, plan_queries(g, resolve(g, [F("MixedCase", "val")]))[0], g)
    assert cols == ["MixedCase.val", "MixedCase.id"] and rows == [("m", 1)]


def test_no_row_multiplication(conn):
    g = graph_of(conn)
    qs = plan_queries(g, resolve(g, [F("a", "name"), F("b", "label")], "a"))
    _, main_rows = run(conn, qs[0], g)
    assert len(main_rows) == 2  # one per `a`, not 3 (one per `b`)


def test_composite_key_join(conn):
    g = graph_of(conn)
    qs = plan_queries(g, resolve(g, [F("e", "id"), F("d", "info")]))
    cols, rows = run(conn, qs[0], g)
    assert rows == [(5, "info-11")]


def test_start_without_pk_runs(conn):
    g = graph_of(conn)
    qs = plan_queries(g, resolve(g, [F("nokey", "v")]))
    _, rows = run(conn, qs[0], g)
    assert sorted(rows) == [("nk1",), ("nk2",)]


def test_limit_is_applied(conn):
    g = graph_of(conn)
    qs = plan_queries(g, resolve(g, [F("b", "label")]))
    _, rows = run(conn, qs[0], g, limit=2)
    assert len(rows) == 2


def test_fields_two_levels_down_get_an_intermediate_linked_query():
    g = mk_graph(
        {"a": ["id", "x"], "b": ["id", "a_id"], "c": ["id", "b_id", "z"]},
        [("b_a", "b", ["a_id"], "a", ["id"], False), ("c_b", "c", ["b_id"], "b", ["id"], False)],
        pks={"a": ["id"], "b": ["id"], "c": ["id"]},
    )
    qs = plan(g, [F("a", "x"), F("c", "z")], start="a")
    assert [(q.id, q.root, q.parent_query) for q in qs] == [
        ("main", "a", None),
        ("linked-1", "b", "main"),
        ("linked-2", "c", "linked-1"),
    ]
    assert aliases(qs[1]) == ["b.a_id", "b.id"]  # merge key up, key referenced by the child
