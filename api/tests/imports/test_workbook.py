import datetime
import io

from openpyxl import Workbook as XlWorkbook

from app.imports.workbook import read_workbook


def _to_file(wb: XlWorkbook) -> io.BytesIO:
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _single(rows, title="S"):
    wb = XlWorkbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    return _to_file(wb)


def test_sheets_and_rows():
    wb = XlWorkbook()
    ws = wb.active
    ws.title = "first"
    ws.append(["id", "name"])
    ws.append([1, "x"])
    ws.append([2.5, True])
    ws2 = wb.create_sheet("second")
    ws2.append(["k"])
    ws2.append([12.0])
    book, issues = read_workbook(_to_file(wb))
    assert issues == []
    assert [s.name for s in book.sheets] == ["first", "second"]
    assert book.sheets[0].headers == ["id", "name"]
    assert book.sheets[0].rows == [["1", "x"], ["2.5", "true"]]
    assert book.sheets[1].rows == [["12"]]


def test_rows_padded_and_trailing_empty_rows_dropped():
    f = _single([["a", "b"], ["1"], [None, None], ["2", "3"], [None, None]])
    book, _ = read_workbook(f)
    assert book.sheets[0].rows == [["1", ""], ["", ""], ["2", "3"]]


def test_data_under_headerless_column_is_kept_with_issue():
    f = _single([["a", "b"], ["2", "3", "extra"]])
    book, issues = read_workbook(f)
    assert book.sheets[0].headers == ["a", "b", ""]
    assert book.sheets[0].rows == [["2", "3", "extra"]]
    assert [(i.column, i.message) for i in issues] == [("#3", "blank header")]


def test_date_cells_become_iso():
    f = _single([["d", "t"], [datetime.date(2025, 3, 1), datetime.datetime(2025, 3, 1, 10, 30)]])
    book, _ = read_workbook(f)
    assert book.sheets[0].rows == [["2025-03-01", "2025-03-01T10:30:00"]]


def test_datetime_midnight_is_date_only():
    f = _single([["d"], [datetime.datetime(2025, 3, 1, 0, 0, 0)]])
    book, _ = read_workbook(f)
    assert book.sheets[0].rows == [["2025-03-01"]]


def test_blank_and_duplicate_headers_are_issues():
    f = _single([["a", "", "a", " "], ["1", "2", "3", "4"]])
    book, issues = read_workbook(f)
    sheet = book.sheets[0]
    assert len(sheet.headers) == 4
    assert sheet.rows == [["1", "2", "3", "4"]]
    assert [(i.column, i.row, i.sheet) for i in issues] == [
        ("#2", 1, "S"),
        ("a", 1, "S"),
        ("#4", 1, "S"),
    ]
    assert "blank header" in issues[0].message
    assert "duplicate header 'a'" in issues[1].message
    assert "blank header" in issues[2].message


def test_empty_sheet():
    book, issues = read_workbook(_single([]))
    assert book.sheets[0].headers == []
    assert book.sheets[0].rows == []
    assert issues == []


def test_thai_text_and_leading_zeros_preserved():
    f = _single([["name", "code"], ["สวัสดี", "0105536000011"]])
    book, _ = read_workbook(f)
    assert book.sheets[0].rows == [["สวัสดี", "0105536000011"]]
