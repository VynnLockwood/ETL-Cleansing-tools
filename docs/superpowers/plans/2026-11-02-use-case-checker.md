# Use Case Checker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner define use cases (a name plus fields from many tables), press Test, and see whether the data is reachable through foreign keys, with live previews, an ERD, and recommendations for what is missing.

**Architecture:** A new package `api/app/usecases/` reads the foreign-key graph from the Postgres catalog, resolves a start table and shortest join paths (cost model: many-to-one 1, one-to-many 3), splits the join tree at every one-to-many edge into one main query and linked queries, composes SQL with `psycopg.sql`, previews it read-only as `spp_reader`, and applies six fixed recommendation rules. A second web page drives it.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 + psycopg_pool (existing, no new dependencies), plain HTML/JS with an SVG ERD drawn by the page.

**Spec:** `docs/superpowers/specs/2026-11-02-use-case-checker-design.md`

## Global Constraints

- All routes need the admin login; JSON is lowerCamel (pydantic `CamelModel` from `app/imports/models.py`).
- Every identifier in generated SQL comes from the catalog and is composed with `psycopg.sql.Identifier`; no endpoint accepts SQL.
- Preview runs as role `spp_reader` through a second connection pool, in a read-only transaction with `SET LOCAL statement_timeout = '5s'` and `LIMIT 50` (constants `preview_limit=50`, `preview_timeout_ms=5000` in config).
- Cost model: traversing a foreign key from the table that holds it to the table it points at costs 1; the other way costs 3; one-to-one (key columns are the primary key or a unique set of the holding table) costs 1 both ways.
- No query ever joins across a one-to-many edge.
- Schema `spp` only; ordinary and partitioned tables only.
- No new Python dependencies; code stays Python 3.12-compatible; tests use `TEST_DATABASE_URL` and `role_dsn()` from `api/tests/conftest.py`.
- Commits use the repository's configured git identity; do NOT add Co-Authored-By or session trailers.

## Review Focus

1. A saved field whose table or column was dropped later: the test returns status `missing` for it and still checks the rest (pinned in Task 8).
2. A use case with zero fields, or the same field twice: save returns 422 with a clear message (pinned in Task 3).
3. Composite foreign keys, self-referencing keys and two keys between the same pair of tables: no infinite loop, no duplicate join alias, equal-cost paths flagged ambiguous (pinned in Task 4).
4. Mixed-case and odd names (`sppProject`, names with a double quote, Thai names): SQL is quoted correctly; a start table with no primary key still works (pinned in Task 5).
5. A slow or failing query: only that query returns an `error`, the test as a whole still succeeds; empty tables return zero rows without error (pinned in Task 6).

## File Structure

```
api/app/migrations/0005_use_cases.sql
api/app/config.py                      (modify: reader url, preview constants)
api/app/main.py                        (modify: reader pool, include router)
api/app/usecases/__init__.py
api/app/usecases/graph.py              (catalog -> SchemaGraph)
api/app/usecases/models.py             (UseCase models, TestResult models)
api/app/usecases/store.py              (use case persistence)
api/app/usecases/resolver.py           (start table, shortest paths)
api/app/usecases/planner.py            (split into queries, compose SQL)
api/app/usecases/preview.py            (read-only execution)
api/app/usecases/recommend.py          (six rules)
api/app/usecases/erd.py                (ERD data)
api/app/usecases/check.py              (orchestrates a test)
api/app/usecases/router.py             (HTTP routes)
api/tests/usecases/                    (one test file per module)
frontend/usecases.html  frontend/erd.js  frontend/erd.test.js
```

---

### Task 1: Migration 0005, reader pool and preview config

**Files:**
- Create: `api/app/migrations/0005_use_cases.sql`
- Modify: `api/app/config.py`, `api/app/main.py`, `docker-compose.yml` (api service env), `.env.example` (comment only if needed)
- Test: `api/tests/usecases/test_migration.py`, `api/tests/test_config.py` (new cases), `api/tests/test_main.py` (new case)

**Interfaces:**
- Produces: tables `app.use_case(id uuid pk default gen_random_uuid(), name text not null, button_label text, note text, start_table text, created_at timestamptz not null default now(), updated_at timestamptz not null default now())` and `app.use_case_field(id uuid pk default gen_random_uuid(), use_case_id uuid not null references app.use_case(id) on delete cascade, position int not null, table_name text not null, column_name text not null, unique (use_case_id, table_name, column_name))`; `spp_api` has DML on both (explicit GRANT, like migration 0002).
- `Config` gains `reader_database_url: str = ""`, `preview_limit: int = 50`, `preview_timeout_ms: int = 5000`. `load_config` fills `reader_database_url` from env `READER_DATABASE_URL`, else from `SPP_READER_PASSWORD` (+ `SPP_READER_USER` default `spp_reader`, same host variables as the other roles) via the existing `_conninfo` helper, else `""` (no error).
- `create_app` opens `app.state.reader_pool` (via `db.open_pool`) when `cfg.reader_database_url` is non-empty, else sets it to `None`; the pool is closed on shutdown.

- [ ] **Step 1: Write failing tests:** `test_use_case_tables_exist_and_cascade` (insert a use case and a field as `spp_api`; deleting the use case deletes the field; duplicate `(use_case_id, table_name, column_name)` raises `UniqueViolation`), `test_spp_reader_cannot_write_use_case_tables`, `test_reader_url_from_components` and `test_reader_url_optional` (config), `test_reader_pool_created_when_configured` (create_app with `reader_database_url` set has a non-None `app.state.reader_pool`; with `""` it is `None`).
- [ ] **Step 2: Run** `cd api && python -m pytest tests/usecases/test_migration.py tests/test_config.py tests/test_main.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** per the interfaces. Add `SPP_READER_PASSWORD: ${SPP_READER_PASSWORD:?set in .env}` to the `api` service environment in `docker-compose.yml`; validate with `docker compose config -q` using a temporary `.env` copied from `.env.example` (do not commit `.env`).
- [ ] **Step 4: Run** the full suite `cd api && python -m pytest -q -W error` (with `TEST_DATABASE_URL`) — Expected: PASS.
- [ ] **Step 5: Commit** `feat: use case tables, reader pool and preview config`.

### Task 2: Catalog graph and schema endpoint

**Files:**
- Create: `api/app/usecases/__init__.py`, `api/app/usecases/graph.py`, `api/app/usecases/router.py`
- Modify: `api/app/main.py` (include the router under `admin_auth`)
- Test: `api/tests/usecases/test_graph.py`, `api/tests/usecases/test_schema_route.py`

**Interfaces:**
- Produces in `graph.py` (frozen dataclasses):
  - `Column(name: str, sql_type: str, is_pk: bool, is_fk: bool)`
  - `Table(name: str, columns: tuple[Column, ...], pk: tuple[str, ...], unique_sets: tuple[tuple[str, ...], ...])`
  - `Edge(name: str, child: str, parent: str, child_cols: tuple[str, ...], parent_cols: tuple[str, ...], one_to_one: bool)` — `name` is the constraint name; `one_to_one` is true when `child_cols` equals `pk` or one of `unique_sets` of the child.
  - `SchemaGraph(tables: dict[str, Table], edges: tuple[Edge, ...])`
  - `load_graph(conn: psycopg.Connection) -> SchemaGraph` — reads schema `spp` from `pg_catalog` (not `information_schema`, which hides tables the role cannot read): ordinary (`r`) and partitioned (`p`) tables, columns with `format_type`, primary keys, unique constraints and unique indexes without expressions, foreign keys (all columns, in order). Edges sorted by `(child, parent, name)`; tables sorted by name.
- Produces in `router.py`: `build_router(admin: Callable) -> APIRouter` with `GET /schema/tables` -> `[{name, columns:[{name, sqlType, isPrimaryKey, isForeignKey}]}]` (camelCase). It loads the graph from `request.app.state.reader_pool` and returns `503 {"detail": "reader database not configured"}` when that pool is `None`.

- [ ] **Step 1: Write failing tests** against small hand-made tables created in `spp` by the test (`a(id pk)`, `b(id pk, a_id fk a)`, `c(id pk, b_id unique fk b)` one-to-one, `d(x, y, primary key (x, y))`, `e(x, y, foreign key (x, y) references d)` composite, `f(id pk, parent_id fk f)` self-reference, a table with mixed-case name `"MixedCase"` and one with a double quote in its name): `test_tables_and_columns`, `test_primary_and_unique_sets`, `test_edges_direction_and_names`, `test_one_to_one_detected`, `test_composite_fk_is_one_edge_with_ordered_columns`, `test_self_reference_edge`, `test_ignores_other_schemas_and_views`, `test_load_graph_on_real_dictionary` (apply `docs/data-dict-spp.xlsx` schema-only as in `tests/imports/test_apply.py::test_real_dictionary_applies_cleanly`; assert 58 tables and 15 edges), `test_schema_tables_route` (200, camelCase, 401 without credentials, 503 when no reader pool).
- [ ] **Step 2: Run** `python -m pytest tests/usecases/test_graph.py tests/usecases/test_schema_route.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `load_graph`, the router and the `main.py` include.
- [ ] **Step 4: Run** the same tests — Expected: PASS.
- [ ] **Step 5: Commit** `feat: schema graph from the catalog and schema tables endpoint`.

### Task 3: Use case storage and CRUD

**Files:**
- Create: `api/app/usecases/models.py`, `api/app/usecases/store.py`
- Modify: `api/app/usecases/router.py`
- Test: `api/tests/usecases/test_store.py`, `api/tests/usecases/test_usecase_routes.py`

**Interfaces:**
- Consumes: `CamelModel`, `Issue` from `app/imports/models.py`; `load_graph`, `SchemaGraph` from `graph.py`.
- Produces in `models.py`: `FieldRef(table: str, column: str)`; `UseCaseIn(name: str, button_label: str | None = None, note: str | None = None, start_table: str | None = None, fields: list[FieldRef])`; `UseCase(id: uuid.UUID, name, button_label, note, start_table, fields: list[FieldRef], created_at: datetime, updated_at: datetime)`.
- Produces in `store.py`: `validate(graph: SchemaGraph, body: UseCaseIn) -> list[Issue]` (issues use `sheet=table`, `column`, `row=0`: unknown table, unknown column, duplicate field, empty name, no fields, unknown `start_table`); `create(pool, body) -> UseCase`; `get(pool, id: uuid.UUID) -> UseCase | None`; `list_all(pool) -> list[UseCase]` (ordered by `created_at`); `update(pool, id, body) -> UseCase | None`; `delete(pool, id) -> bool`. Field order is the `position`.
- Routes added: `GET /usecases`, `POST /usecases` (201), `GET /usecases/{id}`, `PUT /usecases/{id}`, `DELETE /usecases/{id}` (204). Validation failures return `422 {"issues": [...]}`; unknown or malformed id returns 404; when no reader pool exists validation uses `app.state.pool` for the catalog read.

- [ ] **Step 1: Write failing tests:** `test_validate_rejects_unknown_table_column_duplicate_and_empty` (each case a separate issue message), `test_start_table_must_exist`, `test_create_get_update_delete_roundtrip` (order of fields kept; `updated_at` changes on update), `test_delete_cascades_fields`, `test_routes_crud_and_camelcase`, `test_routes_422_and_404_and_malformed_id`, `test_routes_require_auth`, `test_zero_fields_is_422`, `test_same_field_twice_is_422`.
- [ ] **Step 2: Run** `python -m pytest tests/usecases/test_store.py tests/usecases/test_usecase_routes.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `models.py`, `store.py` and the routes; use parameterised SQL only.
- [ ] **Step 4: Run** the same tests — Expected: PASS.
- [ ] **Step 5: Commit** `feat: use case storage and CRUD routes`.

### Task 4: Resolver (start table and shortest paths)

**Files:**
- Create: `api/app/usecases/resolver.py`
- Test: `api/tests/usecases/test_resolver.py` (pure: graphs built in the test as `SchemaGraph` objects, no database)

**Interfaces:**
- Consumes: `SchemaGraph`, `Edge`, `FieldRef`.
- Produces (frozen dataclasses):
  - `Step(edge: Edge, direction: Literal["up", "down"])` — `up` goes child to parent; `down` goes parent to child; `Step.cost` is 1 for `up`, 1 for `down` when `edge.one_to_one`, else 3; `Step.fan_out` is true for a `down` step with `one_to_one` false; `Step.label` is `"<child>.<cols> -> <parent>"`.
  - `Path(table: str, steps: tuple[Step, ...], cost: int, ambiguous: bool, alternatives: tuple[str, ...])` — `alternatives` are labels of up to 3 other equal-cost paths (joined step labels).
  - `FieldStatus(field: FieldRef, status: Literal["ok", "unreachable", "missing"], path: Path | None)`.
  - `Resolution(start: str, start_reason: str, paths: dict[str, Path], fields: tuple[FieldStatus, ...])` — `paths` holds every table reached from the start (the start has empty `steps`).
  - `resolve(graph: SchemaGraph, fields: Sequence[FieldRef], start_override: str | None = None) -> Resolution`.
- Rules: a field whose table or column is not in `graph` is `missing` (and does not count as unreachable). Start table (no override): try every table that owns a non-missing field; pick the minimum of `(unreachable field count, fan_out step count over needed paths, total path cost of needed paths, table name)`; `start_reason` reads like `"reaches 6 of 7 fields with 1 linked query"`. Paths: Dijkstra from the start over all edges in both directions, ties broken by edge name so the result is a deterministic shortest-path tree; an equal-cost alternative arrival at a needed table sets `ambiguous`. A self-referencing edge is never used to reach a new table. With no non-missing fields and no override, `start` is `""` and `paths` is empty.

- [ ] **Step 1: Write failing tests:** `test_cost_model` (up 1, down 3, one-to-one down 1), `test_start_table_prefers_fewest_unreachable_then_fewest_fanout`, `test_override_start_table_wins`, `test_unreachable_field_status`, `test_missing_field_status_not_counted_unreachable`, `test_deterministic_tie_break_by_edge_name`, `test_equal_cost_paths_flag_ambiguous_with_alternatives`, `test_two_keys_between_same_pair_is_ambiguous`, `test_composite_edge_is_single_step`, `test_self_reference_does_not_loop_or_reach`, `test_paths_form_a_tree` (every reached table has exactly one parent step), `test_reason_text`, `test_no_fields`.
- [ ] **Step 2: Run** `python -m pytest tests/usecases/test_resolver.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `resolve` with `heapq`; no database access.
- [ ] **Step 4: Run** the same tests — Expected: PASS.
- [ ] **Step 5: Commit** `feat: use case resolver with start table choice and shortest paths`.

### Task 5: Query planner and SQL composer

**Files:**
- Create: `api/app/usecases/planner.py`
- Test: `api/tests/usecases/test_planner.py` (plan tests are pure; SQL tests run on a database)

**Interfaces:**
- Consumes: `SchemaGraph`, `Resolution`, `Step`, `FieldRef`.
- Produces:
  - `SelectItem(table: str, column: str, alias: str)` (`alias` is `"<table>.<column>"`).
  - `QuerySpec(id: str, title: str, kind: Literal["main", "linked"], root: str, joins: tuple[Step, ...], select: tuple[SelectItem, ...], merge: tuple[tuple[str, str], ...], parent_query: str | None)` — `merge` pairs `(root_column, parent_column)` for linked queries (empty for main); `id` is `"main"` or `"linked-1"`, `"linked-2"` … in path order.
  - `plan_queries(graph: SchemaGraph, resolution: Resolution) -> list[QuerySpec]` — only `ok` fields are selected. Rule: a table belongs to the query of the **last fan-out step** on its path (main if none); a linked query is rooted at the child table of that fan-out step, its `joins` are the steps after it (all `up` or one-to-one; one `LEFT JOIN` per step in path order, deduplicated by step prefix when several fields share a prefix), its `merge` is the fan-out edge's `child_cols`/`parent_cols`, and its `parent_query` is the query of the table the fan-out step started from. Tables sharing the same fan-out step prefix share one query. The main query also selects the start table's primary-key columns (if any) once.
  - `compose(spec: QuerySpec, graph: SchemaGraph, limit: int) -> psycopg.sql.Composed` — `SELECT ... FROM <root> AS t0 LEFT JOIN ... LIMIT <limit>` with every name through `sql.Identifier`, aliases `t0, t1, ...` in join order, select aliases `"table.column"`, join conditions comparing the ordered column lists.

- [ ] **Step 1: Write failing tests:** pure: `test_one_to_many_becomes_linked_query_with_merge_key`, `test_many_to_one_chain_stays_in_main`, `test_second_fan_out_inside_branch_starts_another_linked_query` (with `parent_query` set), `test_tables_sharing_a_fan_out_share_one_query`, `test_one_to_one_down_stays_in_same_query`, `test_main_selects_start_pk_once`, `test_start_without_primary_key_works`, `test_unreachable_and_missing_fields_not_selected`. With a database (`clean_db`, small tables with rows incl. `"MixedCase"` and a name containing a double quote): `test_compose_runs_and_returns_expected_rows` (main query returns one row per start row with joined parent values; linked query returns the child rows and merge-key values), `test_compose_quotes_odd_identifiers`, `test_no_row_multiplication` (start has 2 children: main row count equals start row count), `test_composite_key_join`.
- [ ] **Step 2: Run** `python -m pytest tests/usecases/test_planner.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `plan_queries` and `compose`.
- [ ] **Step 4: Run** the same tests — Expected: PASS.
- [ ] **Step 5: Commit** `feat: split a resolution into main and linked queries and compose SQL`.

### Task 6: Preview runner

**Files:**
- Create: `api/app/usecases/preview.py`
- Test: `api/tests/usecases/test_preview.py`

**Interfaces:**
- Consumes: `QuerySpec`, `compose`, `SchemaGraph`, a `ConnectionPool` for `spp_reader`.
- Produces: `QueryResult(id: str, title: str, kind: str, merge_key: list[str], sql: str, columns: list[str], rows: list[list[object]], truncated: bool, error: str | None)` (pydantic `CamelModel`) and `run_preview(pool: ConnectionPool, graph: SchemaGraph, specs: Sequence[QuerySpec], limit: int, timeout_ms: int) -> list[QueryResult]`. Each query runs on its own connection from the pool in a transaction set `READ ONLY` with `SET LOCAL statement_timeout`; the composed query asks for `limit + 1` rows so `truncated` is exact (the extra row is dropped); `sql` is the displayed text with the limit shown as `limit`; values are made JSON-safe (`Decimal`/`uuid` to `str`, date/datetime to ISO, `bytes` to hex, `memoryview` likewise). Any `psycopg.Error` becomes `error` (short message, no traceback) for that query only; other queries still run.

- [ ] **Step 1: Write failing tests:** `test_rows_and_columns`, `test_truncated_flag_exact` (limit equals row count: not truncated; one more row: truncated), `test_json_safe_values`, `test_empty_table_returns_no_rows_no_error`, `test_timeout_gives_error_for_that_query_only` (use a tiny timeout and a query made slow with `pg_sleep` via a table function wrapper in the test, or a huge cross join; the next query still succeeds), `test_read_only_transaction_rejects_writes` (a spec cannot write, but assert the connection is read-only by running `SHOW transaction_read_only` through the runner's connection helper), `test_permission_denied_becomes_error` (revoke SELECT on one table from `spp_reader` inside the test).
- [ ] **Step 2: Run** `python -m pytest tests/usecases/test_preview.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `run_preview`; expose the read-only connection helper as `read_only_connection(pool, timeout_ms)` (a context manager) for reuse by Task 7.
- [ ] **Step 4: Run** the same tests — Expected: PASS.
- [ ] **Step 5: Commit** `feat: read-only preview runner with timeout and truncation`.

### Task 7: Recommendations

**Files:**
- Create: `api/app/usecases/recommend.py`
- Modify: `api/app/usecases/models.py` (add `Fix`, `Evidence`, `Recommendation`)
- Test: `api/tests/usecases/test_recommend.py`

**Interfaces:**
- Consumes: `SchemaGraph`, `Resolution`, `QuerySpec`, `read_only_connection` from `preview.py`.
- Produces in `models.py` (pydantic `CamelModel`): `Fix(table: str, column: str, ref_table: str, ref_column: str)`, `Evidence(matched: int, total: int)`, `Recommendation(code: str, severity: Literal["error", "warning", "info"], message: str, table: str | None = None, field: FieldRef | None = None, fix: Fix | None = None, evidence: Evidence | None = None)`.
- Produces in `recommend.py`: `recommend(pool: ConnectionPool, graph: SchemaGraph, resolution: Resolution, queries: Sequence[QuerySpec], timeout_ms: int) -> list[Recommendation]`, ordered by severity (error, warning, info) then code then table. Rules (the "reached set" is the keys of `resolution.paths`; an "unreachable table" owns an `unreachable` field):
  - `NO_PATH_KEY_MATCH` (error): for each unreachable table `U`, candidate pairs are (a) a non-PK column of `U` against the single-column primary key of a reached table, and (b) a non-PK column of a reached table against `U`'s single-column primary key; only pairs of the same type family (uuid; integer family; text/varchar; numeric) are tried. Evidence is `matched` = count of the column's non-null values present in the other key, `total` = count of non-null values (computed in SQL with `::text` comparison). A pair qualifies when `total > 0 and matched == total`, or when `total == 0` and the column name (case-insensitive) contains the other table's name; the message states "no data to confirm" in the second case and mentions differing types if the types are in the same family but not identical. One recommendation per unreachable table: the pair with the highest `matched`, then by column name. The `fix` points from the holding column to the key.
  - `MISSING_TABLE` (warning): a column of a reached table whose name matches `^mt[A-Z]\w*$`, which has no foreign key and no table of exactly that name — one recommendation per such column.
  - `NO_PRIMARY_KEY` (error): an unreachable table with no primary key and no unique set.
  - `AMBIGUOUS_PATH` (warning): for each `Path` with `ambiguous`, naming the used path and its `alternatives`.
  - `ISOLATED_TABLE` (error): an unreachable table with no edge in or out and no `NO_PATH_KEY_MATCH` recommendation.
  - `FAN_OUT` (info): one per linked query: "`<root>` has many rows per `<parent table>`; shown as a separate query".

- [ ] **Step 1: Write failing tests** (small tables with data, `clean_db`, reader pool via `role_dsn`): per rule one test that triggers it and one that does not — `test_key_match_suggests_fk_with_evidence`, `test_key_match_requires_all_values_contained`, `test_key_match_empty_table_uses_name`, `test_key_match_picks_best_candidate_per_table`, `test_key_match_only_same_type_family`, `test_missing_table_for_mt_column`, `test_mt_column_with_fk_or_table_is_quiet`, `test_no_primary_key`, `test_ambiguous_path_lists_alternatives`, `test_isolated_table`, `test_fan_out_info_per_linked_query`, `test_ordering_by_severity`, `test_real_dictionary_address_suggestion` (real dictionary schema-only, empty tables: fields from `sppProject`, `sppProjectAddress` give `NO_PATH_KEY_MATCH` with fix `sppProjectAddress.sppProject -> sppProject.sppProjectID`, and `sppProject.mtZone` gives `MISSING_TABLE`).
- [ ] **Step 2: Run** `python -m pytest tests/usecases/test_recommend.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** the six rules; the evidence queries use `sql.Identifier` and run through `read_only_connection`.
- [ ] **Step 4: Run** the same tests — Expected: PASS.
- [ ] **Step 5: Commit** `feat: recommendation rules for unreachable fields`.

### Task 8: Check orchestration, ERD and the test endpoint

**Files:**
- Create: `api/app/usecases/erd.py`, `api/app/usecases/check.py`
- Modify: `api/app/usecases/models.py` (result models), `api/app/usecases/router.py`
- Test: `api/tests/usecases/test_erd.py`, `api/tests/usecases/test_check.py`, `api/tests/usecases/test_test_route.py`

**Interfaces:**
- Consumes: Tasks 2–7.
- Produces in `models.py` (pydantic `CamelModel`): `FieldResult(table, column, status, query: str | None, via: list[str], reason: str | None)`; `ErdNode(table, role: Literal["start", "reached", "unreachable"], fields: list[str])`; `ErdEdge(from_: str (alias "from"), to: str, columns: list[str], status: Literal["fk", "suggested"], cardinality: Literal["many-to-one", "one-to-many", "one-to-one"])`; `Erd(nodes, edges)`; `TestResult(start_table: str, start_table_reason: str, fields: list[FieldResult], queries: list[QueryResult], recommendations: list[Recommendation], erd: Erd)`.
- `erd.build_erd(graph, resolution, recommendations) -> Erd` — nodes: the start, every reached table that owns a chosen field or lies on a used path, and every unreachable field table; edges: each used step once, plus one `suggested` edge per recommendation `fix`.
- `check.run_check(pool, reader_pool, cfg: Config, uc: UseCase) -> TestResult` — loads the graph (reader pool), `resolve` (override = `uc.start_table`), `plan_queries`, `run_preview(... cfg.preview_limit, cfg.preview_timeout_ms)`, `recommend`, `build_erd`; field `reason` for `unreachable` is "no foreign key path from `<start>`" and for `missing` "table or column no longer exists"; `via` is the path step labels.
- Route: `POST /usecases/{id}/test` -> `TestResult`; `404` for unknown or malformed id; `503` when no reader pool.

- [ ] **Step 1: Write failing tests:** `test_erd_nodes_edges_and_suggested_edge`, `test_check_ok_unreachable_and_missing_fields` (a saved use case whose table was dropped afterwards gets `missing` for that field and the rest still tested — Review Focus 1), `test_check_returns_queries_with_rows`, `test_check_survives_failing_query`, `test_real_schema_success_criterion` (real dictionary schema-only, then insert one row each into `sppBusinessPartner`, `sppProject` and `sppProjectConnection` via SQL; use case with fields `sppProject.projectCode`, `sppBusinessPartner.juristicName`, `sppProjectConnection.egatMainSubstation`, `sppProjectAddress.fullAddress`: partner field `ok` in `main`, connection field `ok` in a `linked` query, address field `unreachable`, a `NO_PATH_KEY_MATCH` recommendation with fix `sppProjectAddress.sppProject -> sppProject.sppProjectID`, the ERD has a `suggested` edge and a `FAN_OUT` info), `test_test_route_shape_camelcase_and_errors` (200 shape; 404; malformed id 404; 401; 503 without reader pool).
- [ ] **Step 2: Run** `python -m pytest tests/usecases -q` — Expected: FAIL for the new tests.
- [ ] **Step 3: Implement** `erd.py`, `check.py` and the route; the `by_alias` JSON for `ErdEdge` must emit `from`.
- [ ] **Step 4: Run** the full suite `python -m pytest -q -W error` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: use case test endpoint with ERD and recommendations`.

### Task 9: Web page

**Files:**
- Create: `frontend/usecases.html`, `frontend/erd.js`, `frontend/erd.test.js`
- Modify: `frontend/index.html` (one link), `frontend/Dockerfile` (copy the new files if it copies files individually), `nginx/nginx.conf` (only if static routing needs it)

**Interfaces:**
- Consumes: the API of Tasks 2, 3 and 8 (JSON contract in the spec, section 7) with the same Basic auth header pattern as `frontend/index.html`.
- Produces `erd.js`: `layoutErd(nodes, edges, startTable) -> {positions: {[table]: {x, y}}, width: number, height: number}` (pure; layers by breadth-first distance from the start table over the edges treated as undirected, unreachable nodes in a last layer; stable order by table name) exported with `module.exports` when `module` exists and as a global otherwise.

- [ ] **Step 1: Write failing JS tests** in `erd.test.js` (`node --test`): `layers by distance`, `unreachable nodes in last layer`, `positions do not overlap`, `stable order`, `single node`, `empty`.
- [ ] **Step 2: Run** `node --test frontend/erd.test.js` — Expected: FAIL.
- [ ] **Step 3: Implement** `erd.js`, then `usecases.html`: use case list with New/Delete; editor (name, button label, note, optional start table dropdown, table dropdown then column dropdown filled from `/api/schema/tables`, Add, field list with remove and up/down reorder, Save via POST or PUT); **Test** button showing (a) fields with green/red status and reasons, (b) recommendation cards (error/warning/info) with the suggested key, (c) the ERD as SVG built with `document.createElementNS` (solid lines for foreign keys, red dashed for suggested), (d) one tab per query with the SQL and preview rows (`truncated` noted). Server text only via `textContent`; errors show `issues` or `detail` like `index.html`; Save and Test buttons are disabled while a request runs. Add a link to `usecases.html` in `index.html` and back.
- [ ] **Step 4: Run** `node --test frontend/erd.test.js` — Expected: PASS; also `node --check` on the inline script (extract it) and an `html.parser` well-formedness check on both pages.
- [ ] **Step 5: Commit** `feat: use case checker web page`.

### Task 10: End-to-end step and docs

**Files:**
- Modify: `scripts/e2e.py`, `README.md`, `docs/superpowers/HANDOFF.md`, `docs/superpowers/specs/2026-11-02-use-case-checker-design.md` (status line only)

**Interfaces:**
- Consumes: the full API. Extends the existing stdlib-only script; step 9 added after the data-load steps.

- [ ] **Step 1: Add the e2e step** (fails first against an API without the feature): create the use case from the spec's success criterion through `POST /usecases` (fields `sppProject.projectCode`, `sppBusinessPartner.juristicName`, `sppProjectConnection.egatMainSubstation`, `sppProjectAddress.fullAddress`), `POST /usecases/{id}/test`, assert: partner field `ok`, connection field `ok` in a linked query, address field `unreachable`, a `NO_PATH_KEY_MATCH` recommendation with the fix `sppProjectAddress.sppProject -> sppProject.sppProjectID`, every query has no `error`; also `GET /schema/tables` returns 58 tables and every new route returns 401 without credentials; delete the use case at the end so the step is re-runnable.
- [ ] **Step 2: Run it** against a locally started API on a clean database as described in the existing README/e2e notes (Postgres on the local test server, start `uvicorn` on port 8081 with `SPP_READER_PASSWORD` set so the reader pool exists; stop the server afterwards) — Expected: all steps pass, twice in a row.
- [ ] **Step 3: Docs:** README section "Use cases" (what it does, how to use the page, the six recommendation codes, limits: preview 50 rows / 5 s, schema `spp` only, no path pinning yet); HANDOFF updated (what is built, what is verified, what is not: nothing Docker-related was run, later items from the spec); spec status line set to "Approved; implemented".
- [ ] **Step 4: Run** the full suite and `bash -n scripts/smoke.sh`, `python -m py_compile scripts/e2e.py` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: end-to-end check and docs for the use case checker`.

## Self-Review Notes

- **Spec coverage:** purpose and success criterion → Tasks 8 and 10; scope list → Tasks 1–10; concepts and cost model → Tasks 2, 4, 5; data model → Task 1; resolver → Task 4; SQL → Task 5; recommendations table → Task 7; API → Tasks 2, 3, 8; live preview → Task 6; web page → Task 9; safety → Global Constraints and Tasks 3, 5, 6; testing → every task plus Task 10; files → File Structure.
- **Decided beyond the spec:** the "other side" for `NO_PATH_KEY_MATCH` is limited to the reached set (bounds the number of evidence queries); `MISSING_TABLE` uses the pattern `^mt[A-Z]\w*$`; preview rows of linked queries are the first rows overall, not restricted to the main preview rows; `reader_database_url` is optional and its absence returns 503 instead of failing startup.
- **Type consistency:** `FieldRef`, `UseCase` (Task 3), `SchemaGraph`/`Edge` (Task 2), `Step`/`Path`/`Resolution` (Task 4), `QuerySpec`/`compose` (Task 5), `QueryResult`/`run_preview`/`read_only_connection` (Task 6), `Recommendation` (Task 7) are used with the same names in Tasks 7 and 8.
