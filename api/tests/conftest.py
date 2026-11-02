import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

# Passwords of the non-owner roles created by postgres/init, overridable for other setups.
ROLE_PASSWORDS = {
    "spp_api": os.environ.get("TEST_SPP_API_PASSWORD", "apipw"),
    "spp_reader": os.environ.get("TEST_SPP_READER_PASSWORD", "readerpw"),
}


def role_dsn(owner_url: str, role: str) -> str:
    """DSN for another role, derived from the owner DSN (same host/port/db)."""
    params = conninfo_to_dict(owner_url)
    params["user"] = role
    params["password"] = ROLE_PASSWORDS[role]
    return make_conninfo(**params)


@pytest.fixture
def db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


@pytest.fixture
def clean_db(db_url: str) -> str:
    with psycopg.connect(db_url, autocommit=True) as conn:
        for schema in ("spp", "app", "usecase"):
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.execute(f"CREATE SCHEMA {schema}")
    return db_url
