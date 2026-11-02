from app.imports.infer import profile_sheet
from app.imports.keys import detect_keys
from app.imports.models import Sheet, Workbook


def run(sheets, threshold=0.99):
    wb = Workbook(sheets=sheets)
    return detect_keys(wb, [profile_sheet(s) for s in sheets], threshold)


def parent(n=100):
    return Sheet(
        "sppProject",
        ["sppProjectID", "title"],
        [[str(i), f"t{i}"] for i in range(1, n + 1)],
    )


def test_primary_key_by_name():
    p = run([Sheet("sppProject", ["title", "SPPPROJECTID"], [["a", "1"], ["b", "1"]])])
    assert p.tables[0].primary_key == ["SPPPROJECTID"]


def test_primary_key_by_sole_unique_column():
    p = run([Sheet("t", ["code", "kind"], [["a", "x"], ["b", "x"], ["c", "x"]])])
    assert p.tables[0].primary_key == ["code"]


def test_no_pk_when_several_unique_columns():
    p = run([Sheet("t", ["a", "b"], [["1", "x"], ["2", "y"]])])
    assert p.tables[0].primary_key == []


def test_foreign_key_by_name():
    child = Sheet("task", ["taskID", "sppProjectID"], [["1", "1"], ["2", "1"], ["3", "2"]])
    p = run([parent(5), child])
    assert len(p.foreign_keys) == 1
    fk = p.foreign_keys[0]
    assert (fk.table, fk.column, fk.ref_table, fk.ref_column) == (
        "task", "sppProjectID", "sppProject", "sppProjectID")
    assert fk.source == "name" and fk.unmatched == 0 and fk.accepted


def test_foreign_key_by_containment():
    child = Sheet("task", ["taskID", "sppProject"], [[str(i), str(i % 5 + 1)] for i in range(10)])
    p = run([parent(5), child])
    assert [(f.column, f.source, f.accepted) for f in p.foreign_keys] == [
        ("sppProject", "containment", True)]


def test_foreign_key_by_containment_with_orphans():
    rows = [[str(i), str(i)] for i in range(1, 99)] + [["98", "900"], ["99", "901"]]
    child = Sheet("task", ["taskID", "sppProject"], rows)
    p = run([parent(100), child], threshold=0.95)
    fk = p.foreign_keys[0]
    assert fk.source == "containment" and fk.unmatched == 2 and fk.accepted is False


def test_name_and_containment_reported_once():
    child = Sheet("task", ["taskID", "sppProjectID"], [["1", "1"], ["2", "2"]])
    p = run([parent(5), child])
    assert len(p.foreign_keys) == 1 and p.foreign_keys[0].source == "name"


def test_does_not_propose_fk_when_no_values_overlap():
    child = Sheet("task", ["taskID", "sppProjectID"], [["1", "900"], ["2", "901"]])
    assert run([parent(5), child]).foreign_keys == []


def test_shared_pk_name_does_not_create_fk():
    a = Sheet("a", ["id"], [["1"], ["2"]])
    b = Sheet("b", ["id"], [["1"], ["2"]])
    assert run([a, b]).foreign_keys == []


def test_no_keys_for_empty_table():
    p = run([Sheet("t", ["a", "b"], [])])
    assert p.tables[0].primary_key == [] and p.foreign_keys == []


def test_columns_carry_profiles_and_json_is_camel():
    s = parent(2)
    p = run([s])
    assert p.tables[0].columns == profile_sheet(s).columns
    d = p.model_dump(by_alias=True)
    assert set(d) == {"tables", "foreignKeys", "addAudit", "schemaOnly"}
    assert d["tables"][0]["primaryKey"] == ["sppProjectID"]


def test_low_cardinality_integer_column_not_auto_accepted():
    child = Sheet("task", ["taskID", "status"], [[str(i), str(i % 3 + 1)] for i in range(10)])
    p = run([parent(200), child])
    assert all(not f.accepted for f in p.foreign_keys)


def test_containment_with_table_name_in_column_is_accepted():
    child = Sheet("task", ["taskID", "sppProject"], [["1", "1"], ["2", "2"]])
    fk = run([parent(5), child]).foreign_keys[0]
    assert fk.source == "containment" and fk.accepted is True


def test_one_fk_per_column():
    ps = [Sheet(n, [f"{n}ID"], [["1"], ["2"], ["3"]]) for n in ("a", "b", "c")]
    child = Sheet("task", ["taskID", "level"], [["1", "1"], ["2", "2"]])
    fks = run(ps + [child]).foreign_keys
    assert [f.column for f in fks if f.table == "task"] == ["level"]


def test_ambiguous_candidates_not_accepted():
    a = Sheet("projA", ["projAID"], [["1"], ["2"]])
    b = Sheet("projB", ["projBID"], [["1"], ["2"]])
    child = Sheet("task", ["taskID", "projA"], [["1", "1"], ["2", "2"]])
    # "projA" is contained in both; only projA is corroborated but they tie on rank.
    fks = run([a, b, child]).foreign_keys
    assert len(fks) == 1 and fks[0].accepted is False


def test_below_threshold_containment_is_rejected():
    rows = [[str(i), str(i if i <= 7 else i + 1000)] for i in range(1, 11)]
    child = Sheet("task", ["taskID", "sppProject"], rows)
    assert run([parent(100), child]).foreign_keys == []


def test_same_table_candidate_skipped():
    s = Sheet("emp", ["empID", "manager"], [["1", "1"], ["2", "1"]])
    assert run([s]).foreign_keys == []


def test_name_match_is_case_insensitive():
    child = Sheet("task", ["taskID", "SPPPROJECTID"], [["1", "1"], ["2", "2"]])
    fk = run([parent(5), child]).foreign_keys[0]
    assert fk.source == "name" and fk.accepted and fk.ref_column == "sppProjectID"
