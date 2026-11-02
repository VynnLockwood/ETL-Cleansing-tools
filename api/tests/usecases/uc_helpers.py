"""Shared test helpers for the use case checker tests (plain module, not a conftest)."""

from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Config
from app.imports.apply import apply as apply_import
from app.imports.dictionary import parse_dictionary
from app.imports.models import Workbook
from app.imports.workbook import read_workbook
from app.main import create_app
from conftest import role_dsn

AUTH = ("admin", "pw")

SMALL_SCHEMA_SQL = """
CREATE TABLE spp.a (id int PRIMARY KEY, name text);
CREATE TABLE spp.b (id int PRIMARY KEY, a_id int REFERENCES spp.a (id), label text);
CREATE TABLE spp.c (id int PRIMARY KEY, b_id int UNIQUE REFERENCES spp.b (id), note text);
CREATE TABLE spp.d (x int, y int, info text, PRIMARY KEY (x, y));
CREATE TABLE spp.e (id int PRIMARY KEY, x int, y int, FOREIGN KEY (x, y) REFERENCES spp.d (x, y));
CREATE TABLE spp.f (id int PRIMARY KEY, parent_id int REFERENCES spp.f (id));
CREATE TABLE spp."MixedCase" (id int PRIMARY KEY, val text);
CREATE TABLE spp."we""ird" (id int PRIMARY KEY, a_id int REFERENCES spp.a (id), "co""l" text);
CREATE TABLE spp.nokey (v text);
CREATE VIEW spp.v AS SELECT * FROM spp.a;
CREATE TABLE app.other (id int PRIMARY KEY);
"""


def make_small_schema(url: str) -> str:
    """Migrate, then create the small hand-made schema as the owner."""
    db.migrate(url)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SMALL_SCHEMA_SQL)
    return url


def real_dictionary():
    path = Path(__file__).resolve().parents[3] / "docs" / "data-dict-spp.xlsx"
    if not path.exists():
        pytest.skip("real dictionary not available")
    with path.open("rb") as f:
        wb, _ = read_workbook(f)
    return parse_dictionary(wb)[0]


def load_real_schema(url: str):
    """Migrate and create the real 58-table schema (no rows). Returns the proposal."""
    p = real_dictionary()
    db.migrate(url)
    pool = db.open_pool(url)
    try:
        r = apply_import(pool, p, Workbook(sheets=[]))
    finally:
        pool.close()
    assert r.issues == []
    return p


def make_config(url: str, tmp_path, reader: bool = True) -> Config:
    return Config(
        database_url=url,
        migrate_database_url=url,
        admin_password="pw",
        upload_dir=str(tmp_path),
        reader_database_url=role_dsn(url, "spp_reader") if reader else "",
    )


@contextmanager
def client_for(url: str, tmp_path, reader: bool = True):
    with TestClient(create_app(make_config(url, tmp_path, reader))) as client:
        yield client


SMALL_DATA_SQL = """
INSERT INTO spp.a VALUES (1, 'A1'), (2, 'A2');
INSERT INTO spp.b VALUES (10, 1, 'b-10'), (11, 1, 'b-11'), (12, 2, 'b-12');
INSERT INTO spp.c VALUES (100, 10, 'note-100');
INSERT INTO spp.d VALUES (1, 1, 'info-11');
INSERT INTO spp.e VALUES (5, 1, 1);
INSERT INTO spp."MixedCase" VALUES (1, 'm');
INSERT INTO spp."we""ird" VALUES (1, 1, 'cv');
INSERT INTO spp.nokey VALUES ('nk1'), ('nk2');
"""


def make_small_data(url: str) -> str:
    make_small_schema(url)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SMALL_DATA_SQL)
    return url


def mk_graph(tables, edges, pks=None):
    """Pure SchemaGraph for resolver/planner tests.

    tables: {name: [columns]}; edges: [(name, child, child_cols, parent, parent_cols, one_to_one)];
    pks: {table: [pk columns]}
    """
    from app.usecases.graph import Column, Edge, SchemaGraph, Table

    pks = pks or {}
    ts = {
        n: Table(
            n,
            tuple(Column(c, "text", c in pks.get(n, []), False) for c in cols),
            tuple(pks.get(n, [])),
            (),
        )
        for n, cols in tables.items()
    }
    es = tuple(Edge(nm, ch, pa, tuple(cc), tuple(pc), o) for nm, ch, cc, pa, pc, o in edges)
    return SchemaGraph(ts, es)


def F(table, column):
    from app.usecases.models import FieldRef

    return FieldRef(table=table, column=column)


def make_use_case(fields, start=None, name="uc"):
    import uuid
    from datetime import datetime, timezone

    from app.usecases.models import UseCase

    now = datetime.now(timezone.utc)
    return UseCase(
        id=uuid.uuid4(), name=name, start_table=start, fields=list(fields),
        created_at=now, updated_at=now,
    )
