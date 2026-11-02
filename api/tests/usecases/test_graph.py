import psycopg

from app.usecases.graph import load_graph
from uc_helpers import load_real_schema, make_small_schema


def _graph(clean_db):
    make_small_schema(clean_db)
    with psycopg.connect(clean_db) as conn:
        return load_graph(conn)


def test_tables_and_columns(clean_db):
    g = _graph(clean_db)
    assert sorted(g.tables) == ["MixedCase", "a", "b", "c", "d", "e", "f", "nokey", 'we"ird']
    cols = {c.name: c for c in g.tables["b"].columns}
    assert [c.name for c in g.tables["b"].columns] == ["id", "a_id", "label"]
    assert cols["id"].sql_type == "integer" and cols["id"].is_pk and not cols["id"].is_fk
    assert cols["a_id"].is_fk and not cols["a_id"].is_pk
    assert cols["label"].sql_type == "text" and not cols["label"].is_fk


def test_primary_and_unique_sets(clean_db):
    g = _graph(clean_db)
    assert g.tables["d"].pk == ("x", "y")
    assert g.tables["nokey"].pk == ()
    assert ("b_id",) in g.tables["c"].unique_sets


def test_edges_direction_and_names(clean_db):
    g = _graph(clean_db)
    e = next(e for e in g.edges if e.child == "b" and e.parent == "a")
    assert e.child_cols == ("a_id",) and e.parent_cols == ("id",)
    assert e.name == "b_a_id_fkey"
    assert [(x.child, x.parent, x.name) for x in g.edges] == sorted(
        (x.child, x.parent, x.name) for x in g.edges
    )


def test_one_to_one_detected(clean_db):
    g = _graph(clean_db)
    by_child = {e.child: e for e in g.edges}
    assert by_child["c"].one_to_one is True
    assert by_child["b"].one_to_one is False


def test_composite_fk_is_one_edge_with_ordered_columns(clean_db):
    g = _graph(clean_db)
    edges = [e for e in g.edges if e.child == "e"]
    assert len(edges) == 1
    assert edges[0].child_cols == ("x", "y") and edges[0].parent_cols == ("x", "y")


def test_self_reference_edge(clean_db):
    g = _graph(clean_db)
    e = next(e for e in g.edges if e.child == "f")
    assert e.parent == "f" and e.child_cols == ("parent_id",)


def test_ignores_other_schemas_and_views(clean_db):
    g = _graph(clean_db)
    assert "v" not in g.tables and "other" not in g.tables


def test_load_graph_on_real_dictionary(clean_db):
    load_real_schema(clean_db)
    with psycopg.connect(clean_db) as conn:
        g = load_graph(conn)
    assert len(g.tables) == 58
    assert len(g.edges) == 15
    assert any(e.child == "sppProject" and e.parent == "sppBusinessPartner" for e in g.edges)
