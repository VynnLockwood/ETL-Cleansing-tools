from fastapi.testclient import TestClient

from app import db
from app.config import Config
from app.main import create_app


class StubPool:
    def close(self):
        pass


def test_healthz(monkeypatch):
    monkeypatch.setattr(db, "migrate", lambda url: [])
    monkeypatch.setattr(db, "open_pool", lambda url: StubPool())
    cfg = Config("postgresql://x", "postgresql://x", "pw", "/tmp")
    with TestClient(create_app(cfg)) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        assert isinstance(client.app.state.pool, StubPool)


def test_docs_pages_are_not_served(monkeypatch):
    monkeypatch.setattr(db, "migrate", lambda url: [])
    monkeypatch.setattr(db, "open_pool", lambda url: StubPool())
    cfg = Config("postgresql://x", "postgresql://x", "pw", "/tmp")
    with TestClient(create_app(cfg)) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path
