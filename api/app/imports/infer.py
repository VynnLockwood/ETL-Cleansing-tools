import re
import uuid
from datetime import datetime

from app.imports.models import ColumnProfile, Sheet, TableProfile

_INT_RE = re.compile(r"-?\d+")
_DEC_RE = re.compile(r"-?\d+\.\d+")
_LEADING_ZERO_RE = re.compile(r"-?0\d")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")

_INT32_MAX = 2**31 - 1
_INT64_MAX = 2**63 - 1
_MAX_SAMPLES = 5


def _is_bool(v: str) -> bool:
    return v.lower() in ("true", "false")


def _is_uuid(v: str) -> bool:
    return bool(_UUID_RE.fullmatch(v))


def _is_int(v: str) -> bool:
    return bool(_INT_RE.fullmatch(v)) and not _LEADING_ZERO_RE.match(v)


def _is_numeric(v: str) -> bool:
    if _is_int(v):
        return True
    return bool(_DEC_RE.fullmatch(v)) and not _LEADING_ZERO_RE.match(v)


def _is_timestamp(v: str) -> bool:
    if not _TS_RE.fullmatch(v):
        return False
    try:
        datetime.strptime(v.replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return True


def _is_date(v: str) -> bool:
    if not _DATE_RE.fullmatch(v):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _text_type(values: list[str]) -> str:
    longest = max(len(v) for v in values)
    for n in (50, 255, 512):
        if longest <= n:
            return f"varchar({n})"
    return "text"


def _sql_type(values: list[str]) -> str:
    if not values:
        return "text"
    if all(_is_bool(v) for v in values):
        return "boolean"
    if all(_is_uuid(v) for v in values):
        return "uuid"
    if all(_is_int(v) for v in values):
        biggest = max(abs(int(v)) for v in values)
        if biggest <= _INT32_MAX:
            return "integer"
        if biggest <= _INT64_MAX:
            return "bigint"
        return "numeric(%d,0)" % max(len(v.lstrip("-")) for v in values)
    if all(_is_numeric(v) for v in values):
        int_digits = frac_digits = 0
        for v in values:
            whole, _, frac = v.lstrip("-").partition(".")
            int_digits = max(int_digits, len(whole))
            frac_digits = max(frac_digits, len(frac))
        return f"numeric({int_digits + frac_digits},{frac_digits})"
    if all(_is_timestamp(v) for v in values):
        return "timestamp"
    if all(_is_date(v) for v in values):
        return "date"
    return _text_type(values)


def infer_column(name: str, values: list[str]) -> ColumnProfile:
    """Profile one column. Empty or whitespace-only strings count as null."""
    present = [v for v in values if v.strip() != ""]
    seen: dict[str, None] = dict.fromkeys(present)
    return ColumnProfile(
        name=name,
        sql_type=_sql_type(present),
        non_null=len(present),
        distinct=len(seen),
        unique=len(seen) == len(present),
        samples=list(seen)[:_MAX_SAMPLES],
    )


def profile_sheet(sheet: Sheet) -> TableProfile:
    columns = []
    for i, header in enumerate(sheet.headers):
        values = [row[i] if i < len(row) else "" for row in sheet.rows]
        columns.append(infer_column(header, values))
    return TableProfile(name=sheet.name, columns=columns, row_count=len(sheet.rows))
