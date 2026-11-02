"""Models of the use case checker (camelCase JSON)."""

import uuid
from typing import Literal
from datetime import datetime

from pydantic import ConfigDict
from pydantic.alias_generators import to_camel

from app.imports.models import CamelModel


class FieldRef(CamelModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    table: str
    column: str


class UseCaseIn(CamelModel):
    name: str
    button_label: str | None = None
    note: str | None = None
    start_table: str | None = None
    fields: list[FieldRef]


class UseCase(CamelModel):
    id: uuid.UUID
    name: str
    button_label: str | None = None
    note: str | None = None
    start_table: str | None = None
    fields: list[FieldRef]
    created_at: datetime
    updated_at: datetime


class QueryResult(CamelModel):
    id: str
    title: str
    kind: str
    merge_key: list[str]
    sql: str
    columns: list[str]
    rows: list[list[object]]
    truncated: bool
    error: str | None = None


class Fix(CamelModel):
    table: str
    column: str
    ref_table: str
    ref_column: str


class Evidence(CamelModel):
    matched: int
    total: int


class Recommendation(CamelModel):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    table: str | None = None
    field: FieldRef | None = None
    fix: Fix | None = None
    evidence: Evidence | None = None
