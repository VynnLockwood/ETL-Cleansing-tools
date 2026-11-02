from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base for all proposal/result models: lowerCamel JSON, snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


@dataclass
class Sheet:
    name: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Workbook:
    sheets: list[Sheet] = field(default_factory=list)


class Issue(CamelModel):
    sheet: str
    column: str
    row: int
    message: str


class ColumnProfile(CamelModel):
    name: str
    sql_type: str
    non_null: int
    distinct: int
    unique: bool
    samples: list[str]


class TableProfile(CamelModel):
    name: str
    columns: list[ColumnProfile]
    row_count: int


class ForeignKey(CamelModel):
    table: str
    column: str
    ref_table: str
    ref_column: str
    source: str  # "name" | "containment" | "dictionary"
    unmatched: int
    accepted: bool


class TableProposal(CamelModel):
    name: str
    columns: list[ColumnProfile]
    primary_key: list[str]


class Proposal(CamelModel):
    tables: list[TableProposal]
    foreign_keys: list[ForeignKey]
    add_audit: bool = False
    schema_only: bool = False
