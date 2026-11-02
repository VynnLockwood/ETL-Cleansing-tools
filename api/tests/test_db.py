import psycopg

from app import db


def test_migrate_creates_schemas(clean_db):
    applied = db.migrate(clean_db)
    assert applied[0] == "0001_schemas_roles"
    with psycopg.connect(clean_db) as conn:
        names = {r[0] for r in conn.execute("SELECT schema_name FROM information_schema.schemata")}
    assert {"spp", "app", "usecase"} <= names


def test_migrate_is_idempotent(clean_db):
    db.migrate(clean_db)
    assert db.migrate(clean_db) == []
