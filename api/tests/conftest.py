import os

import psycopg
import pytest


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
