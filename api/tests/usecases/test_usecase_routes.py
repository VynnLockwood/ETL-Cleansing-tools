import uuid

from uc_helpers import AUTH, client_for, make_small_schema

GOOD = {
    "name": "Project detail",
    "buttonLabel": "Open",
    "note": "n",
    "startTable": None,
    "fields": [{"table": "b", "column": "label"}, {"table": "a", "column": "name"}],
}


def test_routes_crud_and_camelcase(clean_db, tmp_path):
    make_small_schema(clean_db)
    with client_for(clean_db, tmp_path) as c:
        r = c.post("/usecases", json=GOOD, auth=AUTH)
        assert r.status_code == 201
        uc = r.json()
        assert uc["buttonLabel"] == "Open" and "createdAt" in uc and "updatedAt" in uc
        assert [f["table"] for f in uc["fields"]] == ["b", "a"]
        assert c.get("/usecases", auth=AUTH).json()[0]["id"] == uc["id"]
        assert c.get(f"/usecases/{uc['id']}", auth=AUTH).json()["name"] == "Project detail"
        put = dict(GOOD, name="Renamed", startTable="b")
        r = c.put(f"/usecases/{uc['id']}", json=put, auth=AUTH)
        assert r.status_code == 200 and r.json()["name"] == "Renamed"
        assert r.json()["startTable"] == "b"
        assert c.delete(f"/usecases/{uc['id']}", auth=AUTH).status_code == 204
        assert c.get(f"/usecases/{uc['id']}", auth=AUTH).status_code == 404


def test_routes_422_and_404_and_malformed_id(clean_db, tmp_path):
    make_small_schema(clean_db)
    with client_for(clean_db, tmp_path) as c:
        bad = dict(GOOD, fields=[{"table": "zz", "column": "x"}])
        r = c.post("/usecases", json=bad, auth=AUTH)
        assert r.status_code == 422
        assert r.json()["issues"][0]["message"] == "unknown table 'zz'"
        assert r.json()["issues"][0]["sheet"] == "zz"
        missing = str(uuid.uuid4())
        assert c.get(f"/usecases/{missing}", auth=AUTH).status_code == 404
        assert c.put(f"/usecases/{missing}", json=GOOD, auth=AUTH).status_code == 404
        assert c.delete(f"/usecases/{missing}", auth=AUTH).status_code == 404
        assert c.get("/usecases/not-a-uuid", auth=AUTH).status_code == 404
        assert c.put("/usecases/not-a-uuid", json=GOOD, auth=AUTH).status_code == 404
        assert c.delete("/usecases/not-a-uuid", auth=AUTH).status_code == 404


def test_zero_fields_is_422(clean_db, tmp_path):
    make_small_schema(clean_db)
    with client_for(clean_db, tmp_path) as c:
        r = c.post("/usecases", json=dict(GOOD, fields=[]), auth=AUTH)
        assert r.status_code == 422
        assert r.json()["issues"][0]["message"] == "at least one field is required"


def test_same_field_twice_is_422(clean_db, tmp_path):
    make_small_schema(clean_db)
    with client_for(clean_db, tmp_path) as c:
        dup = dict(GOOD, fields=[{"table": "a", "column": "name"}, {"table": "a", "column": "name"}])
        r = c.post("/usecases", json=dup, auth=AUTH)
        assert r.status_code == 422
        assert "duplicate field a.name" in [i["message"] for i in r.json()["issues"]]


def test_update_validation_failure_keeps_old_data(clean_db, tmp_path):
    make_small_schema(clean_db)
    with client_for(clean_db, tmp_path) as c:
        uc = c.post("/usecases", json=GOOD, auth=AUTH).json()
        r = c.put(f"/usecases/{uc['id']}", json=dict(GOOD, name=""), auth=AUTH)
        assert r.status_code == 422
        assert c.get(f"/usecases/{uc['id']}", auth=AUTH).json()["name"] == "Project detail"


def test_routes_require_auth(clean_db, tmp_path):
    make_small_schema(clean_db)
    some = str(uuid.uuid4())
    with client_for(clean_db, tmp_path) as c:
        assert c.get("/usecases").status_code == 401
        assert c.post("/usecases", json=GOOD).status_code == 401
        assert c.get(f"/usecases/{some}").status_code == 401
        assert c.put(f"/usecases/{some}", json=GOOD).status_code == 401
        assert c.delete(f"/usecases/{some}").status_code == 401


def test_validation_works_without_reader_pool(clean_db, tmp_path):
    make_small_schema(clean_db)
    with client_for(clean_db, tmp_path, reader=False) as c:
        assert c.post("/usecases", json=GOOD, auth=AUTH).status_code == 201
