import datetime
from typing import IO, Any

from openpyxl import load_workbook

from .models import Issue, Sheet, Workbook


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime.datetime):
        if value.time() == datetime.time(0, 0):
            return value.date().isoformat()
        return value.isoformat(timespec="seconds")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _read_sheet(name: str, raw_rows: list[tuple]) -> tuple[Sheet, list[Issue]]:
    if not raw_rows:
        return Sheet(name=name), []
    rows = [[_cell(v) for v in r] for r in raw_rows]
    # drop trailing fully-empty rows
    while rows and not any(c != "" for c in rows[-1]):
        rows.pop()
    if not rows:
        return Sheet(name=name), []
    max_width = max(len(r) for r in rows)
    header_row = rows[0] + [""] * (max_width - len(rows[0]))
    data = rows[1:]
    # trim trailing columns that are completely empty (no header, no data)
    width = len(header_row)
    while width > 0 and header_row[width - 1] == "" and all(
        (r[width - 1] if width - 1 < len(r) else "") == "" for r in data
    ):
        width -= 1
    headers = header_row[:width]
    rows_out = [(r + [""] * width)[:width] for r in data]

    issues: list[Issue] = []
    seen: set[str] = set()
    for i, h in enumerate(headers, start=1):
        if h.strip() == "":
            issues.append(Issue(sheet=name, column=f"#{i}", row=1, message="blank header"))
        elif h in seen:
            issues.append(
                Issue(sheet=name, column=h, row=1, message=f"duplicate header '{h}'")
            )
        else:
            seen.add(h)
    return Sheet(name=name, headers=headers, rows=rows_out), issues


def _raw_rows(ws) -> list[tuple]:
    # Ignore the declared <dimension>: some exporters write ref="A1" or omit it.
    ws.reset_dimensions()
    return list(ws.iter_rows(values_only=True))


def read_workbook(f: IO[bytes]) -> tuple[Workbook, list[Issue]]:
    wb = load_workbook(f, read_only=True, data_only=True)
    try:
        sheets: list[Sheet] = []
        issues: list[Issue] = []
        for ws in wb.worksheets:
            sheet, sheet_issues = _read_sheet(ws.title, _raw_rows(ws))
            sheets.append(sheet)
            issues.extend(sheet_issues)
        return Workbook(sheets=sheets), issues
    finally:
        wb.close()
