import uuid

import psycopg
import pytest

from app import db
from app.usecases import store
from app.usecases.graph import load_graph
from app.usecases.models import FieldRef, UseCaseIn
from uc_helpers import make_small_schema


@pytest.fixture
def graph(clean_db):
    make_small_schema(clean_db)
    with psycopg.connect(clean_db) as conn:
        return load_graph(conn)


@pytest.fixture
def pool(clean_db, graph):
    p = db.open_pool(clean_db)
    yield p
    p.close()


def body(**kw):
    base = dict(name="Detail", fields=[FieldRef(table="a", column="name")])
    base.update(kw)
    return UseCaseIn(**base)


def messages(issues):
    return [i.message for i in issues]


def test_validate_accepts_good_body(graph):
    assert store.validate(graph, body()) == []


def test_validate_rejects_unknown_table_column_duplicate_and_empty(graph):
    bad = body(
        name="  ",
        fields=[
            FieldRef(table="nope", column="x"),
            FieldRef(table="a", column="nope"),
            FieldRef(table="a", column="name"),
            FieldRef(table="a", column="name"),
        ],
    )
    msgs = messages(store.validate(graph, bad))
    assert "name is required" in msgs
    assert "unknown table 'nope'" in msgs
    assert "unknown column 'nope' in table 'a'" in msgs
    assert "duplicate field a.name" in msgs


def test_zero_fields_is_invalid(graph):
    assert "at least one field is required" in messages(
        store.validate(graph, body(fields=[]))
    )


def test_start_table_must_exist(graph):
    assert "unknown start table 'zzz'" in messages(
        store.validate(graph, body(start_table="zzz"))
    )
    assert store.validate(graph, body(start_table="b")) == []


def test_create_get_update_delete_roundtrip(pool):
    created = store.create(
        pool,
        body(
            button_label="Open",
            note="n",
            fields=[FieldRef(table="b", column="label"), FieldRef(table="a", column="name")],
        ),
    )
    assert [f.table for f in created.fields] == ["b", "a"]
    assert store.get(pool, created.id).name == "Detail"
    assert [u.id for u in store.list_all(pool)] == [created.id]
    updated = store.update(
        pool,
        created.id,
        body(name="Renamed", fields=[FieldRef(table="a", column="name"), FieldRef(table="b", column="id")]),
    )
    assert updated.name == "Renamed"
    assert [(f.table, f.column) for f in updated.fields] == [("a", "name"), ("b", "id")]
    assert updated.updated_at > created.updated_at
    assert updated.created_at == created.created_at
    assert store.delete(pool, created.id) is True
    assert store.get(pool, created.id) is None
    assert store.delete(pool, created.id) is False
    assert store.update(pool, created.id, body()) is None


def test_list_ordered_by_creation(pool):
    first = store.create(pool, body(name="one"))
    second = store.create(pool, body(name="two"))
    assert [u.id for u in store.list_all(pool)] == [first.id, second.id]


def test_delete_cascades_fields(pool, clean_db):
    created = store.create(pool, body())
    store.delete(pool, created.id)
    with psycopg.connect(clean_db) as conn:
        assert conn.execute("SELECT count(*) FROM app.use_case_field").fetchone()[0] == 0
