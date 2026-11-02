import importlib.util
from pathlib import Path
from urllib.parse import unquote, urlsplit

CONFIG = Path(__file__).resolve().parents[2] / "superset" / "superset_config.py"


def _load(monkeypatch, password):
    monkeypatch.setenv("SUPERSET_SECRET_KEY", "k")
    monkeypatch.setenv("SUPERSET_DB_PASSWORD", password)
    spec = importlib.util.spec_from_file_location("superset_config_under_test", CONFIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_space_is_percent_20_and_everything_round_trips(monkeypatch):
    pw = "p/a@s%s:w d#x+y"
    mod = _load(monkeypatch, pw)
    assert mod.userinfo("a b") == "a%20b"
    parts = urlsplit(mod.SQLALCHEMY_DATABASE_URI)
    assert "+" not in parts.password.replace("%2B", "")
    assert unquote(parts.password) == pw
    assert (parts.hostname, parts.port, parts.path) == ("postgres", 5432, "/superset_meta")
