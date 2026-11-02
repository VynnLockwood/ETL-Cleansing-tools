import base64

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import admin_auth


def _client() -> TestClient:
    app = FastAPI()

    @app.get("/secret", dependencies=[Depends(admin_auth("s3cret"))])
    def secret() -> dict[str, str]:
        return {"ok": "yes"}

    return TestClient(app)


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_rejects_missing_and_wrong():
    client = _client()
    for headers in (
        {},
        _basic("admin", "wrong"),
        _basic("root", "s3cret"),
    ):
        resp = client.get("/secret", headers=headers)
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"].startswith("Basic")


def test_allows_correct():
    resp = _client().get("/secret", headers=_basic("admin", "s3cret"))
    assert resp.status_code == 200
    assert resp.json() == {"ok": "yes"}
