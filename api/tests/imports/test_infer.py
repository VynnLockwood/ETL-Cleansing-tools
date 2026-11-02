from app.imports.infer import infer_column, profile_sheet
from app.imports.models import Sheet


def t(values):
    return infer_column("c", values).sql_type


def test_boolean():
    assert t(["true", "FALSE", "True"]) == "boolean"
    assert t(["0", "1"]) == "integer"
    assert t(["true", "yes"]) == "varchar(50)"


def test_integer_and_bigint():
    assert t(["1", "20"]) == "integer"
    assert t(["3000000000"]) == "bigint"
    assert t(["-5", "0", ""]) == "integer"


def test_numeric():
    assert t(["12.50", "3.1"]) == "numeric(4,2)"
    assert t(["0.5", "10"]) == "numeric(3,1)"


def test_non_numeric_forms_stay_text():
    assert t(["+5"]) == "varchar(50)"
    assert t(["1,000"]) == "varchar(50)"


def test_date_and_timestamp():
    assert t(["2024-01-31", "2023-12-01"]) == "date"
    assert t(["2024-01-31T10:00:00", "2024-01-31 11:30:59"]) == "timestamp"
    assert t(["2024-01-31", "2024-01-31 10:00:00"]) == "varchar(50)"
    assert t(["31/01/2024"]) == "varchar(50)"
    assert t(["2024-13-45"]) == "varchar(50)"


def test_uuid():
    assert t(["123e4567-e89b-12d3-a456-426614174000"]) == "uuid"


def test_mixed_falls_back_to_varchar():
    assert t(["12", "abc"]) == "varchar(50)"
    assert t(["x" * 51]) == "varchar(255)"
    assert t(["x" * 256]) == "varchar(512)"
    assert t(["x" * 513]) == "text"


def test_leading_zeros_stay_text():
    assert t(["10110", "01234"]) == "varchar(50)"
    assert t(["0105536000011"]) == "varchar(50)"
    assert t(["007"]) == "varchar(50)"
    assert t(["0"]) == "integer"
    assert t(["0.5"]) == "numeric(2,1)"


def test_all_empty_is_text():
    p = infer_column("c", ["", "", "  "])
    assert p.sql_type == "text"
    assert p.non_null == 0 and p.distinct == 0 and p.samples == []


def test_whitespace_only_is_null():
    p = infer_column("c", ["1", " ", ""])
    assert p.sql_type == "integer" and p.non_null == 1


def test_unique():
    assert infer_column("c", ["a", "b", ""]).unique is True
    assert infer_column("c", ["a", "a"]).unique is False


def test_counts_and_samples():
    p = infer_column("c", ["b", "a", "b", "", "c", "d", "e", "f"])
    assert p.non_null == 7 and p.distinct == 6
    assert p.samples == ["b", "a", "c", "d", "e"]


def test_profile_sheet():
    s = Sheet(name="T", headers=["a", "b"], rows=[["1", "x"], ["2", "y"]])
    tp = profile_sheet(s)
    assert tp.name == "T" and tp.row_count == 2
    assert [c.sql_type for c in tp.columns] == ["integer", "varchar(50)"]
    assert tp.model_dump(by_alias=True)["columns"][0]["sqlType"] == "integer"


def test_profile_header_only_sheet():
    tp = profile_sheet(Sheet(name="T", headers=["a", "b"], rows=[]))
    assert tp.row_count == 0
    assert [c.sql_type for c in tp.columns] == ["text", "text"]


def test_profile_ragged_rows():
    tp = profile_sheet(Sheet(name="T", headers=["a", "b"], rows=[["1"], ["2", "x"]]))
    assert tp.columns[1].non_null == 1
