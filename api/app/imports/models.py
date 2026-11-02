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
