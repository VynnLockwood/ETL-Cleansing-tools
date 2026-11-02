from uc_helpers import AUTH, client_for, make_small_schema


def test_schema_tables_route(clean_db, tmp_path):
    make_small_schema(clean_db)
    with client_for(clean_db, tmp_path) as client:
        r = client.get("/schema/tables", auth=AUTH)
        assert r.status_code == 200
        body = r.json()
        b = next(t for t in body if t["name"] == "b")
        assert b["columns"][0] == {
            "name": "id",
            "sqlType": "integer",
            "isPrimaryKey": True,
            "isForeignKey": False,
        }
        assert b["columns"][1]["isForeignKey"] is True


def test_schema_tables_requires_auth(clean_db, tmp_path):
    make_small_schema(clean_db)
    with client_for(clean_db, tmp_path) as client:
        assert client.get("/schema/tables").status_code == 401


def test_schema_tables_503_without_reader_pool(clean_db, tmp_path):
    make_small_schema(clean_db)
    with client_for(clean_db, tmp_path, reader=False) as client:
        r = client.get("/schema/tables", auth=AUTH)
        assert r.status_code == 503
        assert r.json()["detail"] == "reader database not configured"
