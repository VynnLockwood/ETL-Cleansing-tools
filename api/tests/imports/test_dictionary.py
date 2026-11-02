from pathlib import Path

import pytest

from app.imports.dictionary import normalize_type, parse_dictionary
from app.imports.models import Sheet, Workbook
from app.imports.workbook import read_workbook

HEADERS = ["Table", "Column", "Source", "Data type", "หมายเหตุ"]


def wb_of(*rows, extra=None):
    sheets = [Sheet("d", HEADERS, [list(r) + [""] * (5 - len(r)) for r in rows])]
    return Workbook(sheets=sheets + (extra or []))


def table(p, name):
    return next(t for t in p.tables if t.name == name)


def test_types_and_primary_key():
    p, issues = parse_dictionary(wb_of(
        ("t", "tID", "", "uuid"),
        ("t", "a", "", "character varying(255)"),
        ("t", "b", "", "timestamp with time zone"),
        ("t", "c", "", "timestamp without time zone"),
        ("t", "d", "", "numeric(10,2)"),
        ("t", "e", "", "double precision"),
        ("t", "f", "", "text"),
        ("t", "g", "", "boolean"),
        ("t", "h", "", "date"),
        ("t", "i", "", "integer"),
    ))
    t = table(p, "t")
    assert [c.sql_type for c in t.columns] == [
        "uuid", "varchar(255)", "timestamptz", "timestamp", "numeric(10,2)",
        "double precision", "text", "boolean", "date", "integer"]
    assert t.primary_key == ["tID"]
    assert p.schema_only is True
    assert issues == []


def test_foreign_keys():
    p, issues = parse_dictionary(wb_of(
        ("mtVoltageLevel", "mtVoltageLevelID", "", "varchar(10)"),
        ("sppProject", "sppProjectID", "", "uuid"),
        ("sppProject", "mtVoltageLevel", "", "varchar(10)", "FK → mtVoltageLevel"),
        ("sppConn", "sppProject", "", "uuid", "FK → sppProject.sppProjectID"),
    ))
    by_col = {(f.table, f.column): f for f in p.foreign_keys}
    f = by_col[("sppProject", "mtVoltageLevel")]
    assert (f.ref_table, f.ref_column, f.source, f.accepted) == (
        "mtVoltageLevel", "mtVoltageLevelID", "dictionary", True)
    g = by_col[("sppConn", "sppProject")]
    assert (g.ref_table, g.ref_column, g.accepted) == ("sppProject", "sppProjectID", True)
    assert issues == []


def test_audit_columns_folded_into_flag():
    p, _ = parse_dictionary(wb_of(
        ("t", "tID", "", "uuid"),
        ("t", "isDelete", "", "boolean"),
        ("t", "version", "", "integer"),
        ("t", "createdDate", "", "timestamp with time zone"),
        ("t", "updatedBy", "", "uuid"),
        ("t", "deletedDate", "", "timestamp with time zone"),
    ))
    assert p.add_audit is True
    assert [c.name for c in table(p, "t").columns] == ["tID"]
    p2, _ = parse_dictionary(wb_of(("t", "tID", "", "uuid")))
    assert p2.add_audit is False


def test_validation_flags_boolean_date():
    _, issues = parse_dictionary(wb_of(
        ("t", "documentDate", "", "boolean"), ("t", "okDate", "", "date")))
    assert len(issues) == 1
    assert issues[0].column == "documentDate" and issues[0].row == 2


def test_validation_flags_is_not_boolean():
    _, issues = parse_dictionary(wb_of(
        ("t", "isDelete", "", "text"), ("t", "isActive", "", "boolean")))
    assert [i.column for i in issues] == ["isDelete"]


def test_validation_flags_unparseable_type():
    p, issues = parse_dictionary(wb_of(
        ("t", "a", "", "สถานะลบข้อมูล"), ("t", "b", "", "")))
    assert len(issues) == 2
    assert all("unparseable type" in i.message for i in issues)
    assert [c.sql_type for c in table(p, "t").columns] == ["text", "text"]


def test_validation_flags_missing_fk_target():
    p, issues = parse_dictionary(wb_of(
        ("t", "tID", "", "uuid"),
        ("t", "mtX", "", "varchar(10)", "FK → mtX"),
        ("t", "other", "", "uuid", "FK → t.nope"),
    ))
    assert [f.accepted for f in p.foreign_keys] == [False, False]
    assert len(issues) == 2 and all("foreign key" in i.message for i in issues)


def test_pk_fallback_first_uuid_id_column():
    p, _ = parse_dictionary(wb_of(
        ("sppProjectAddress", "addressID", "", "uuid"),
        ("sppProjectAddress", "fullAddress", "", "text")))
    assert table(p, "sppProjectAddress").primary_key == ["addressID"]
    p, _ = parse_dictionary(wb_of(
        ("t", "xID", "", "integer"), ("t", "y", "", "text")))
    assert table(p, "t").primary_key == []


def test_skips_rows_without_table_or_column():
    p, issues = parse_dictionary(wb_of(
        ("", "", "", "varchar(512)"), ("t", "", "", "text"), ("", "c", "", "text"),
        ("t", " a ", "", "text")))
    assert [t.name for t in p.tables] == ["t"]
    assert [c.name for c in table(p, "t").columns] == ["a"]
    assert issues == []


def test_note_from_shifted_last_column_and_non_dictionary_sheet():
    sheet = Sheet("d", HEADERS + ["", "x"], [
        ["t", "tID", "", "uuid", "", "", ""],
        ["t", "p", "", "uuid", "FK → t", "", "migrated"],
    ])
    other = Sheet("data", ["a", "b"], [["1", "2"]])
    p, _ = parse_dictionary(Workbook(sheets=[other, sheet]))
    assert [f.ref_table for f in p.foreign_keys] == ["t"]
    assert [t.name for t in p.tables] == ["t"]


def test_duplicate_column_ignored_with_issue():
    p, issues = parse_dictionary(wb_of(("t", "a", "", "text"), ("t", "a", "", "uuid")))
    assert len(table(p, "t").columns) == 1
    assert len(issues) == 1


@pytest.mark.parametrize("raw,expected", [
    ("character varying", "varchar"), ("numeric", "numeric"), ("jsonb", "jsonb"),
    ("VARCHAR(50)", "varchar(50)"), ("nonsense", None), ("", None),
])
def test_normalize_type(raw, expected):
    assert normalize_type(raw) == expected


REAL = Path(__file__).resolve().parents[3] / "docs" / "data-dict-spp.xlsx"


@pytest.mark.skipif(not REAL.exists(), reason="real dictionary not available")
def test_real_dictionary_parses():
    with REAL.open("rb") as f:
        wb, _ = read_workbook(f)
    p, issues = parse_dictionary(wb)
    names = {t.name for t in p.tables}
    assert {"sppProject", "sppProjectAddition", "sppProjectConnection",
            "sppProjectAddress", "sppBusinessPartner"} <= names
    assert table(p, "sppProject").primary_key == ["sppProjectID"]
    fks = [f for f in p.foreign_keys if f.table == "sppProject"]
    bp = next(f for f in fks if f.ref_table == "sppBusinessPartner")
    assert bp.source == "dictionary" and bp.accepted is True
    missing = [f for f in fks if f.ref_table.startswith("mt") and f.ref_table not in names]
    assert missing, "expected mt* FK targets absent from the dictionary"
    for f in missing:
        assert f.accepted is False
        assert any(i.column == f.column and "foreign key" in i.message
                   for i in issues if i.sheet == "sppProject")
    assert p.add_audit is True
    from app.imports.dictionary import AUDIT_COLUMNS
    assert all(c.name not in AUDIT_COLUMNS for t in p.tables for c in t.columns)
    assert any(i.column == "documentDate" and i.sheet == "sppDocProcExtendScod"
               for i in issues)
    assert p.schema_only is True
