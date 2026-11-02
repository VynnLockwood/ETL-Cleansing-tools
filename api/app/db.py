from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_LOCK_ID = 727001


def open_pool(url: str) -> ConnectionPool:
    pool = ConnectionPool(url, min_size=1, max_size=10, open=False)
    pool.open(wait=True, timeout=30)
    return pool


def migrate(url: str) -> list[str]:
    """Apply pending migrations in filename order; return versions applied."""
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            conn.execute("CREATE SCHEMA IF NOT EXISTS app")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS app.schema_migrations ("
                "version text PRIMARY KEY, "
                "applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM app.schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = path.stem
                if version in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute(
                        "INSERT INTO app.schema_migrations (version) VALUES (%s)",
                        (version,),
                    )
                applied.append(version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied
