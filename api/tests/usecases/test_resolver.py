from app.usecases.graph import Column, Edge, SchemaGraph, Table
from app.usecases.models import FieldRef
from app.usecases.resolver import Step, resolve


def mk(tables, edges):
    """tables: {name: [columns]}; edges: [(name, child, child_cols, parent, parent_cols, one_to_one)]"""
    ts = {
        n: Table(n, tuple(Column(c, "text", False, False) for c in cols), (), ())
        for n, cols in tables.items()
    }
    es = tuple(Edge(nm, ch, pa, tuple(cc), tuple(pc), o) for nm, ch, cc, pa, pc, o in edges)
    return SchemaGraph(ts, es)


def F(table, column):
    return FieldRef(table=table, column=column)


# a <- b <- c  (b.a_id -> a, c.b_id -> b), z isolated
CHAIN = mk(
    {"a": ["id", "name"], "b": ["id", "a_id", "label"], "c": ["id", "b_id", "note"], "z": ["v"]},
    [("b_a", "b", ["a_id"], "a", ["id"], False), ("c_b", "c", ["b_id"], "b", ["id"], False)],
)


def test_cost_model():
    e = Edge("e", "b", "a", ("a_id",), ("id",), False)
    o = Edge("o", "c", "b", ("b_id",), ("id",), True)
    assert Step(e, "up").cost == 1 and not Step(e, "up").fan_out
    assert Step(e, "down").cost == 3 and Step(e, "down").fan_out
    assert Step(o, "down").cost == 1 and not Step(o, "down").fan_out
    assert Step(e, "up").label == "b.a_id -> a"
    assert (Step(e, "up").from_table, Step(e, "up").to_table) == ("b", "a")
    assert (Step(e, "down").from_table, Step(e, "down").to_table) == ("a", "b")


def test_start_table_prefers_fewest_unreachable_then_fewest_fanout():
    r = resolve(CHAIN, [F("a", "name"), F("b", "label"), F("c", "note")])
    assert r.start == "c"
    assert [s.status for s in r.fields] == ["ok", "ok", "ok"]
    assert r.start_reason == "reaches 3 of 3 fields with 0 linked queries"
    assert r.paths["a"].cost == 2 and len(r.paths["a"].steps) == 2


def test_unreachable_counts_before_fanout():
    # d is reachable only from a (d.a_id -> a); start must still be chosen among field tables
    g = mk(
        {"a": ["id"], "d": ["id", "a_id"], "z": ["v"]},
        [("d_a", "d", ["a_id"], "a", ["id"], False)],
    )
    r = resolve(g, [F("a", "id"), F("d", "id"), F("z", "v")])
    assert r.start == "d"  # d reaches a (up) and d; z unreachable for everyone
    assert [s.status for s in r.fields] == ["ok", "ok", "unreachable"]
    assert r.start_reason == "reaches 2 of 3 fields with 0 linked queries"


def test_override_start_table_wins():
    r = resolve(CHAIN, [F("a", "name"), F("c", "note")], start_override="a")
    assert r.start == "a"
    assert r.start_reason.startswith("chosen by you: ")
    assert r.paths["c"].steps[0].fan_out
    assert r.start_reason.endswith("with 2 linked queries")


def test_override_unknown_table_falls_back_to_automatic():
    r = resolve(CHAIN, [F("a", "name"), F("c", "note")], start_override="gone")
    assert r.start == "c"
    assert "'gone' no longer exists" in r.start_reason


def test_unreachable_field_status():
    r = resolve(CHAIN, [F("a", "name"), F("z", "v")])
    z = next(s for s in r.fields if s.field.table == "z")
    assert z.status == "unreachable" and z.path is None
    assert "z" not in r.paths


def test_missing_field_status_not_counted_unreachable():
    r = resolve(CHAIN, [F("a", "name"), F("ghost", "x"), F("a", "nope")])
    assert [s.status for s in r.fields] == ["ok", "missing", "missing"]
    assert r.start_reason == "reaches 1 of 1 fields with 0 linked queries"


def test_deterministic_tie_break_by_edge_name_and_two_keys_between_pair_is_ambiguous():
    g = mk(
        {"a": ["id"], "b": ["id", "a1", "a2"]},
        [("e_b", "b", ["a2"], "a", ["id"], False), ("e_a", "b", ["a1"], "a", ["id"], False)],
    )
    r = resolve(g, [F("a", "id"), F("b", "id")])
    p = r.paths["a"]
    assert p.steps[0].edge.name == "e_a"
    assert p.ambiguous is True
    assert p.alternatives == ("b.a2 -> a",)
    assert r.paths["b"].ambiguous is False


def test_equal_cost_paths_flag_ambiguous_with_alternatives():
    g = mk(
        {"s": ["id"], "x": ["id", "s_id"], "y": ["id", "s_id"], "t": ["id", "x_id", "y_id"]},
        [
            ("x_s", "x", ["s_id"], "s", ["id"], False),
            ("y_s", "y", ["s_id"], "s", ["id"], False),
            ("t_x", "t", ["x_id"], "x", ["id"], False),
            ("t_y", "t", ["y_id"], "y", ["id"], False),
        ],
    )
    r = resolve(g, [F("t", "id"), F("s", "id")], start_override="t")
    assert r.paths["s"].ambiguous is True
    assert r.paths["s"].alternatives == ("t.y_id -> y ; y.s_id -> s",)
    assert r.paths["s"].steps[0].edge.name == "t_x"


def test_composite_edge_is_single_step():
    g = mk(
        {"d": ["x", "y"], "e": ["id", "x", "y"]},
        [("e_d", "e", ["x", "y"], "d", ["x", "y"], False)],
    )
    r = resolve(g, [F("e", "id"), F("d", "x")])
    assert len(r.paths["d"].steps) == 1
    assert r.paths["d"].steps[0].label == "e.x,y -> d"


def test_self_reference_does_not_loop_or_reach():
    g = mk({"f": ["id", "parent_id"]}, [("f_f", "f", ["parent_id"], "f", ["id"], False)])
    r = resolve(g, [F("f", "id")])
    assert r.start == "f" and r.paths["f"].steps == ()
    assert list(r.paths) == ["f"]


def test_paths_form_a_tree():
    r = resolve(CHAIN, [F("a", "name"), F("b", "label"), F("c", "note")])
    for t, p in r.paths.items():
        if p.steps:
            parent = r.paths[p.steps[-1].from_table]
            assert p.steps[:-1] == parent.steps


def test_no_fields():
    r = resolve(CHAIN, [])
    assert r.start == "" and r.paths == {} and r.fields == ()
    r2 = resolve(CHAIN, [F("ghost", "x")])
    assert r2.start == "" and r2.fields[0].status == "missing"


def test_reason_text_singular():
    r = resolve(CHAIN, [F("a", "name"), F("b", "label")], start_override="a")
    assert r.start_reason.endswith("with 1 linked query")
