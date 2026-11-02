import psycopg
import pytest
from psycopg import errors

from app import db
from conftest import role_dsn


def _setup(clean_db):
    db.migrate(clean_db)
    return clean_db


def test_use_case_tables_exist_and_cascade(clean_db):
    url = _setup(clean_db)
    with psycopg.connect(role_dsn(url, "spp_api"), autocommit=True) as conn:
        uc = conn.execute(
            "INSERT INTO app.use_case (name) VALUES ('Detail') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.use_case_field (use_case_id, position, table_name, column_name)"
            " VALUES (%s, 0, 't', 'c')",
            (uc,),
        )
        with pytest.raises(errors.UniqueViolation):
            conn.execute(
                "INSERT INTO app.use_case_field (use_case_id, position, table_name, column_name)"
                " VALUES (%s, 1, 't', 'c')",
                (uc,),
            )
        conn.execute("DELETE FROM app.use_case WHERE id = %s", (uc,))
        left = conn.execute("SELECT count(*) FROM app.use_case_field").fetchone()[0]
        assert left == 0


def test_spp_reader_cannot_write_use_case_tables(clean_db):
    url = _setup(clean_db)
    with psycopg.connect(role_dsn(url, "spp_reader"), autocommit=True) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO app.use_case (name) VALUES ('x')")
