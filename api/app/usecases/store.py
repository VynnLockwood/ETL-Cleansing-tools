"""Persistence of use cases (schema `app`)."""

import uuid

from psycopg_pool import ConnectionPool

from app.imports.models import Issue
from app.usecases.graph import SchemaGraph
from app.usecases.models import FieldRef, UseCase, UseCaseIn

_COLS = "id, name, button_label, note, start_table, created_at, updated_at"


def _issue(table: str, column: str, message: str) -> Issue:
    return Issue(sheet=table, column=column, row=0, message=message)


def validate(graph: SchemaGraph, body: UseCaseIn) -> list[Issue]:
    issues: list[Issue] = []
    if not body.name.strip():
        issues.append(_issue("", "name", "name is required"))
    if not body.fields:
        issues.append(_issue("", "fields", "at least one field is required"))
    if body.start_table is not None and body.start_table not in graph.tables:
        issues.append(_issue(body.start_table, "", f"unknown start table '{body.start_table}'"))
    seen: set[tuple[str, str]] = set()
    for f in body.fields:
        table = graph.tables.get(f.table)
        if table is None:
            issues.append(_issue(f.table, f.column, f"unknown table '{f.table}'"))
            continue
        if f.column not in {c.name for c in table.columns}:
            issues.append(
                _issue(f.table, f.column, f"unknown column '{f.column}' in table '{f.table}'")
            )
            continue
        if (f.table, f.column) in seen:
            issues.append(_issue(f.table, f.column, f"duplicate field {f.table}.{f.column}"))
        seen.add((f.table, f.column))
    return issues


def _insert_fields(conn, uc_id: uuid.UUID, fields: list[FieldRef]) -> None:
    for pos, f in enumerate(fields):
        conn.execute(
            "INSERT INTO app.use_case_field (use_case_id, position, table_name, column_name)"
            " VALUES (%s, %s, %s, %s)",
            (uc_id, pos, f.table, f.column),
        )


def _fields(conn, uc_id: uuid.UUID) -> list[FieldRef]:
    rows = conn.execute(
        "SELECT table_name, column_name FROM app.use_case_field"
        " WHERE use_case_id = %s ORDER BY position",
        (uc_id,),
    ).fetchall()
    return [FieldRef(table=t, column=c) for t, c in rows]


def _build(row, fields: list[FieldRef]) -> UseCase:
    id_, name, label, note, start, created, updated = row
    return UseCase(
        id=id_, name=name, button_label=label, note=note, start_table=start,
        fields=fields, created_at=created, updated_at=updated,
    )


def create(pool: ConnectionPool, body: UseCaseIn) -> UseCase:
    with pool.connection() as conn, conn.transaction():
        row = conn.execute(
            f"INSERT INTO app.use_case (name, button_label, note, start_table)"
            f" VALUES (%s, %s, %s, %s) RETURNING {_COLS}",
            (body.name.strip(), body.button_label, body.note, body.start_table),
        ).fetchone()
        _insert_fields(conn, row[0], body.fields)
        return _build(row, _fields(conn, row[0]))


def get(pool: ConnectionPool, id: uuid.UUID) -> UseCase | None:
    with pool.connection() as conn:
        row = conn.execute(f"SELECT {_COLS} FROM app.use_case WHERE id = %s", (id,)).fetchone()
        if row is None:
            return None
        return _build(row, _fields(conn, id))


def list_all(pool: ConnectionPool) -> list[UseCase]:
    with pool.connection() as conn:
        rows = conn.execute(f"SELECT {_COLS} FROM app.use_case ORDER BY created_at, id").fetchall()
        return [_build(r, _fields(conn, r[0])) for r in rows]


def update(pool: ConnectionPool, id: uuid.UUID, body: UseCaseIn) -> UseCase | None:
    with pool.connection() as conn, conn.transaction():
        row = conn.execute(
            f"UPDATE app.use_case SET name = %s, button_label = %s, note = %s,"
            f" start_table = %s, updated_at = now() WHERE id = %s RETURNING {_COLS}",
            (body.name.strip(), body.button_label, body.note, body.start_table, id),
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM app.use_case_field WHERE use_case_id = %s", (id,))
        _insert_fields(conn, id, body.fields)
        return _build(row, _fields(conn, id))


def delete(pool: ConnectionPool, id: uuid.UUID) -> bool:
    with pool.connection() as conn:
        cur = conn.execute("DELETE FROM app.use_case WHERE id = %s", (id,))
        return cur.rowcount == 1
