import pytest
from psycopg.conninfo import conninfo_to_dict

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


NASTY = "p/a@s%s:w d#x"


def test_components_build_conninfo_with_nasty_passwords():
    cfg = load_config({
        "SPP_API_PASSWORD": NASTY, "SPP_OWNER_PASSWORD": NASTY + "2",
        "ADMIN_PASSWORD": "pw",
    })
    api = conninfo_to_dict(cfg.database_url)
    owner = conninfo_to_dict(cfg.migrate_database_url)
    assert (api["user"], api["password"]) == ("spp_api", NASTY)
    assert (owner["user"], owner["password"]) == ("spp_owner", NASTY + "2")
    assert (api["host"], api["port"], api["dbname"]) == ("postgres", "5432", "spp")


def test_component_overrides():
    cfg = load_config({
        "SPP_API_PASSWORD": "a", "SPP_OWNER_PASSWORD": "o", "ADMIN_PASSWORD": "pw",
        "SPP_DB_HOST": "h", "SPP_DB_PORT": "6543", "SPP_DB_NAME": "d",
        "SPP_API_USER": "u1", "SPP_OWNER_USER": "u2",
    })
    assert conninfo_to_dict(cfg.database_url) == {
        "host": "h", "port": "6543", "dbname": "d", "user": "u1", "password": "a"}
    assert conninfo_to_dict(cfg.migrate_database_url)["user"] == "u2"


def test_database_url_overrides_components():
    cfg = load_config({
        "DATABASE_URL": "postgresql://x", "SPP_API_PASSWORD": "a", "ADMIN_PASSWORD": "pw",
    })
    assert cfg.database_url == cfg.migrate_database_url == "postgresql://x"


def test_components_require_passwords():
    with pytest.raises(ConfigError):
        load_config({"SPP_OWNER_PASSWORD": "o", "ADMIN_PASSWORD": "pw"})
    with pytest.raises(ConfigError):
        load_config({"SPP_API_PASSWORD": "a", "ADMIN_PASSWORD": "pw"})


def test_reader_url_from_components():
    from psycopg.conninfo import conninfo_to_dict

    cfg = load_config(
        {
            "SPP_API_PASSWORD": "a",
            "SPP_OWNER_PASSWORD": "o",
            "SPP_READER_PASSWORD": "r/ead er",
            "ADMIN_PASSWORD": "pw",
        }
    )
    d = conninfo_to_dict(cfg.reader_database_url)
    assert d["user"] == "spp_reader"
    assert d["password"] == "r/ead er"
    assert d["dbname"] == "spp"
    assert cfg.preview_limit == 50
    assert cfg.preview_timeout_ms == 5000


def test_reader_url_optional():
    cfg = load_config(
        {"SPP_API_PASSWORD": "a", "SPP_OWNER_PASSWORD": "o", "ADMIN_PASSWORD": "pw"}
    )
    assert cfg.reader_database_url == ""


def test_reader_database_url_override():
    cfg = load_config(
        {
            "DATABASE_URL": "postgresql://x",
            "READER_DATABASE_URL": "postgresql://reader@h/db",
            "ADMIN_PASSWORD": "pw",
        }
    )
    assert cfg.reader_database_url == "postgresql://reader@h/db"
