import os
from collections.abc import Mapping
from dataclasses import dataclass

from psycopg.conninfo import make_conninfo


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    database_url: str
    migrate_database_url: str
    admin_password: str
    upload_dir: str
    reader_database_url: str = ""
    preview_limit: int = 50
    preview_timeout_ms: int = 5000


def _conninfo(env: Mapping[str, str], user_var: str, user_default: str, password_var: str) -> str:
    """Build a libpq conninfo from separate variables, so passwords need no URL encoding."""
    password = env.get(password_var, "")
    if not password:
        raise ConfigError(f"{password_var} is required (or set DATABASE_URL)")
    return make_conninfo(
        host=env.get("SPP_DB_HOST") or "postgres",
        port=env.get("SPP_DB_PORT") or "5432",
        dbname=env.get("SPP_DB_NAME") or "spp",
        user=env.get(user_var) or user_default,
        password=password,
    )


def load_config(env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        env = os.environ
    database_url = env.get("DATABASE_URL", "")
    if not database_url:
        database_url = _conninfo(env, "SPP_API_USER", "spp_api", "SPP_API_PASSWORD")
    admin_password = env.get("ADMIN_PASSWORD", "")
    if not admin_password:
        raise ConfigError("ADMIN_PASSWORD is required")
    migrate_database_url = env.get("MIGRATE_DATABASE_URL", "")
    if not migrate_database_url:
        if env.get("DATABASE_URL"):
            migrate_database_url = database_url
        else:
            migrate_database_url = _conninfo(env, "SPP_OWNER_USER", "spp_owner", "SPP_OWNER_PASSWORD")
    reader_database_url = env.get("READER_DATABASE_URL", "")
    if not reader_database_url and env.get("SPP_READER_PASSWORD"):
        reader_database_url = _conninfo(env, "SPP_READER_USER", "spp_reader", "SPP_READER_PASSWORD")
    return Config(
        database_url=database_url,
        migrate_database_url=migrate_database_url,
        admin_password=admin_password,
        upload_dir=env.get("UPLOAD_DIR") or "/data/uploads",
        reader_database_url=reader_database_url,
    )
