from app.usecases.erd import build_erd
from app.usecases.models import Fix, Recommendation
from app.usecases.resolver import resolve
from uc_helpers import F, mk_graph

G = mk_graph(
    {"p": ["id", "name"], "c": ["id", "p_id", "v", "x"], "z": ["id", "q"], "mid": ["id", "c_id"], "leaf": ["id", "mid_id", "w"]},
    [
        ("c_p", "c", ["p_id"], "p", ["id"], False),
        ("mid_c", "mid", ["c_id"], "c", ["id"], False),
        ("leaf_mid", "leaf", ["mid_id"], "mid", ["id"], True),
    ],
    pks={"p": ["id"], "c": ["id"], "z": ["id"], "mid": ["id"], "leaf": ["id"]},
)


def rec_with_fix():
    return Recommendation(
        code="NO_PATH_KEY_MATCH", severity="error", message="m", table="z",
        fix=Fix(table="z", column="q", ref_table="p", ref_column="id"),
    )


def test_erd_nodes_edges_and_suggested_edge():
    res = resolve(G, [F("p", "name"), F("c", "v"), F("z", "q")], "p")
    erd = build_erd(G, res, [rec_with_fix()])
    roles = {n.table: n.role for n in erd.nodes}
    assert roles == {"p": "start", "c": "reached", "z": "unreachable"}
    fields = {n.table: n.fields for n in erd.nodes}
    assert fields["c"] == ["v"] and fields["p"] == ["name"] and fields["z"] == ["q"]
    fk = [e for e in erd.edges if e.status == "fk"]
    assert [(e.from_, e.to, e.columns, e.cardinality) for e in fk] == [
        ("p", "c", ["p_id"], "one-to-many")
    ]
    sug = [e for e in erd.edges if e.status == "suggested"]
    assert [(e.from_, e.to, e.columns) for e in sug] == [("z", "p", ["q"])]


def test_intermediate_tables_on_a_used_path_are_nodes():
    res = resolve(G, [F("p", "name"), F("leaf", "w")], "p")
    erd = build_erd(G, res, [])
    assert {n.table for n in erd.nodes} == {"p", "c", "mid", "leaf"}
    cards = {(e.from_, e.to): e.cardinality for e in erd.edges}
    assert cards == {("p", "c"): "one-to-many", ("c", "mid"): "one-to-many", ("mid", "leaf"): "one-to-one"}


def test_many_to_one_cardinality():
    res = resolve(G, [F("c", "v"), F("p", "name")], "c")
    erd = build_erd(G, res, [])
    assert [(e.from_, e.to, e.cardinality) for e in erd.edges] == [("c", "p", "many-to-one")]


def test_suggested_edge_adds_missing_endpoint_nodes():
    res = resolve(G, [F("p", "name")], "p")
    rec = Recommendation(
        code="NO_PATH_KEY_MATCH", severity="error", message="m",
        fix=Fix(table="z", column="q", ref_table="p", ref_column="id"),
    )
    erd = build_erd(G, res, [rec])
    assert {n.table: n.role for n in erd.nodes} == {"p": "start", "z": "unreachable"}


def test_json_uses_from_key():
    res = resolve(G, [F("p", "name"), F("c", "v")], "p")
    data = build_erd(G, res, []).model_dump(by_alias=True, mode="json")
    assert data["edges"][0]["from"] == "p" and "from_" not in data["edges"][0]


def test_empty_resolution_gives_empty_erd():
    erd = build_erd(G, resolve(G, []), [])
    assert erd.nodes == [] and erd.edges == []
