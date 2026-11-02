"""Models of the use case checker (camelCase JSON)."""

import uuid
from typing import Literal
from datetime import datetime

from pydantic import ConfigDict, Field
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


class FieldResult(CamelModel):
    table: str
    column: str
    status: Literal["ok", "unreachable", "missing"]
    query: str | None = None
    via: list[str] = []
    reason: str | None = None


class ErdNode(CamelModel):
    table: str
    role: Literal["start", "reached", "unreachable"]
    fields: list[str]


class ErdEdge(CamelModel):
    from_: str = Field(alias="from")
    to: str
    columns: list[str]
    status: Literal["fk", "suggested"]
    cardinality: Literal["many-to-one", "one-to-many", "one-to-one"]


class Erd(CamelModel):
    nodes: list[ErdNode]
    edges: list[ErdEdge]


class TestResult(CamelModel):
    __test__ = False  # not a pytest test class

    start_table: str
    start_table_reason: str
    fields: list[FieldResult]
    queries: list[QueryResult]
    recommendations: list[Recommendation]
    erd: Erd
