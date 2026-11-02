import uuid

from uc_helpers import AUTH, client_for, make_small_data

UC = {
    "name": "Detail",
    "startTable": "a",
    "fields": [{"table": "a", "column": "name"}, {"table": "b", "column": "label"}],
}


def test_test_route_shape_camelcase_and_errors(clean_db, tmp_path):
    make_small_data(clean_db)
    with client_for(clean_db, tmp_path) as c:
        uc = c.post("/usecases", json=UC, auth=AUTH).json()
        r = c.post(f"/usecases/{uc['id']}/test", auth=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["startTable"] == "a" and "startTableReason" in body
        assert body["fields"][0] == {
            "table": "a", "column": "name", "status": "ok", "query": "main", "via": [], "reason": None,
        }
        assert body["queries"][1]["mergeKey"] == ["b.a_id"]
        assert body["queries"][0]["rows"] and "sql" in body["queries"][0]
        assert body["recommendations"][0]["code"] == "FAN_OUT"
        assert body["erd"]["edges"][0]["from"] == "a" and body["erd"]["edges"][0]["status"] == "fk"
        assert c.post(f"/usecases/{uuid.uuid4()}/test", auth=AUTH).status_code == 404
        assert c.post("/usecases/not-a-uuid/test", auth=AUTH).status_code == 404
        assert c.post(f"/usecases/{uc['id']}/test").status_code == 401


def test_test_route_503_without_reader_pool(clean_db, tmp_path):
    make_small_data(clean_db)
    with client_for(clean_db, tmp_path, reader=False) as c:
        uc = c.post("/usecases", json=UC, auth=AUTH).json()
        r = c.post(f"/usecases/{uc['id']}/test", auth=AUTH)
        assert r.status_code == 503
