import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    database_url: str
    migrate_database_url: str
    admin_password: str
    upload_dir: str


def load_config(env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        env = os.environ
    database_url = env.get("DATABASE_URL", "")
    if not database_url:
        raise ConfigError("DATABASE_URL is required")
    admin_password = env.get("ADMIN_PASSWORD", "")
    if not admin_password:
        raise ConfigError("ADMIN_PASSWORD is required")
    return Config(
        database_url=database_url,
        migrate_database_url=env.get("MIGRATE_DATABASE_URL") or database_url,
        admin_password=admin_password,
        upload_dir=env.get("UPLOAD_DIR") or "/data/uploads",
    )
