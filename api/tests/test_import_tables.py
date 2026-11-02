import uuid

import psycopg
import pytest
from psycopg import errors

from app import db
from conftest import role_dsn


def test_import_tables_exist(clean_db):
    db.migrate(clean_db)
    with psycopg.connect(clean_db, autocommit=True) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'app'"
            )
        }
        assert {"import_job", "import_issue"} <= tables
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO app.import_issue (job_id, message) VALUES (%s, 'x')",
                (uuid.uuid4(),),
            )


def test_import_job_status_check(clean_db):
    db.migrate(clean_db)
    with psycopg.connect(clean_db, autocommit=True) as conn:
        with pytest.raises(errors.CheckViolation):
            conn.execute("INSERT INTO app.import_job (filename, status) VALUES ('a.xlsx', 'bogus')")


def test_spp_api_can_use_import_tables(clean_db):
    db.migrate(clean_db)
    with psycopg.connect(role_dsn(clean_db, "spp_api"), autocommit=True) as conn:
        job_id = conn.execute(
            "INSERT INTO app.import_job (filename, status, proposal) "
            "VALUES ('a.xlsx', 'uploaded', '{}') RETURNING id"
        ).fetchone()[0]
        try:
            conn.execute(
                "INSERT INTO app.import_issue (job_id, sheet, \"column\", row, message) "
                "VALUES (%s, 's', 'c', 1, 'm')",
                (job_id,),
            )
            rows = conn.execute(
                "SELECT message FROM app.import_issue WHERE job_id = %s", (job_id,)
            ).fetchall()
            assert rows == [("m",)]
        finally:
            conn.execute("DELETE FROM app.import_issue WHERE job_id = %s", (job_id,))
            conn.execute("DELETE FROM app.import_job WHERE id = %s", (job_id,))


def test_spp_reader_cannot_write_app_or_spp(clean_db):
    db.migrate(clean_db)
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute("CREATE TABLE spp.probe (id int)")
    with psycopg.connect(role_dsn(clean_db, "spp_reader"), autocommit=True) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO app.import_job (filename, status) VALUES ('a', 'uploaded')")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO spp.probe VALUES (1)")
