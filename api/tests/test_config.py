import pytest

from app.config import ConfigError, load_config


def test_load_requires_database_url():
    with pytest.raises(ConfigError):
        load_config({"ADMIN_PASSWORD": "x"})


def test_load_requires_admin_password():
    with pytest.raises(ConfigError):
        load_config({"DATABASE_URL": "postgresql://x"})


def test_load_defaults():
    cfg = load_config({"DATABASE_URL": "postgresql://x", "ADMIN_PASSWORD": "pw"})
    assert cfg.upload_dir == "/data/uploads"
    assert cfg.migrate_database_url == cfg.database_url
    assert cfg.admin_password == "pw"


def test_load_overrides():
    cfg = load_config({
        "DATABASE_URL": "a", "MIGRATE_DATABASE_URL": "b",
        "ADMIN_PASSWORD": "pw", "UPLOAD_DIR": "/tmp/u",
    })
    assert (cfg.migrate_database_url, cfg.upload_dir) == ("b", "/tmp/u")
