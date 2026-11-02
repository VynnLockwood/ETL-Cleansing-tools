# SPP Stack and Excel Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the Docker stack (nginx, Postgres, Python API, frontend, Superset, Redis) and let the owner import an Excel workbook, review a proposed schema, and have the tables, keys and data created in Postgres.

**Architecture:** Python API (FastAPI, `psycopg` 3, `openpyxl`, plain SQL migrations) owns migrations and the import pipeline: read workbook, profile columns, detect keys (or follow a dictionary), review, apply in one transaction. Superset reads the same database with a read-only role. nginx is the only public entry.

**Tech Stack:** Python 3.12, FastAPI, PostgreSQL 16, Apache Superset, Redis, nginx, Docker Compose, plain HTML/JS frontend.

**Spec:** `docs/superpowers/specs/2026-11-02-spp-service-design.md` (spec sections 3, 4, 6, 7, 8, 9, 10; use-case builder, spec section 5, is a later plan).

## Global Constraints

- PostgreSQL 16; databases `spp` and `superset_meta`; schemas `spp`, `app`, `usecase`.
- Roles: `spp_owner` (migrations, owns schemas), `spp_api` (read/write `spp` and `app`, can create views in `usecase`, no DDL on `spp` outside the import transaction), `spp_reader` (read-only `spp` and `usecase`, used by Superset).
- Only nginx publishes a port (80). Routes: `/` frontend, `/api/` Python API, `/superset/` Superset (`SUPERSET_APP_ROOT`).
- Secrets in `.env` (not in git); `.env.example` committed.
- Foreign keys are real constraints. Apply runs in one transaction and rolls back on any conversion problem.
- Import records live in `app.import_job` and `app.import_issue`.
- Re-import creates new tables only; no destructive changes.
- API auth in v1: one admin login (shared secret from `.env`).
- Identifiers in generated SQL are quoted and come only from the workbook/dictionary after validation, never concatenated unquoted.

## Review Focus

1. Codes with leading zeros (`postCode` `10110`, 13-digit `taxID` starting `0`) must stay text, never integer. Pinned in Task 5.
2. Excel date cells (stored as serial numbers) must arrive as ISO dates, not numbers. Pinned in Task 4.
3. Blank, duplicate or whitespace-only headers must be reported as issues, not crash or silently merge columns. Pinned in Task 4.
4. A sheet with a header but no rows (or no data at all) must not crash profiling or key detection. Pinned in Task 5.
5. Importing the same workbook twice must skip existing tables without error or duplicate rows. Pinned in Task 9.

## File Structure

```
docker-compose.yml  docker-compose.test.yml  .env.example  .gitignore  README.md
scripts/smoke.sh
postgres/init/01-roles.sh
nginx/nginx.conf
api/Dockerfile  api/requirements.txt  api/requirements-dev.txt  api/pytest.ini
api/app/{config,db,main,auth}.py
api/app/migrations/0001_schemas_roles.sql  0002_import_tables.sql  0003_api_create_spp.sql
api/app/imports/models.py       (Sheet, Workbook, Issue, profile + proposal models)
api/app/imports/workbook.py     (read xlsx)
api/app/imports/infer.py        (types)
api/app/imports/keys.py         (PK/FK detection)
api/app/imports/dictionary.py   (dictionary mode + validation)
api/app/imports/ddl.py          (proposal -> DDL)
api/app/imports/apply.py        (transactional create + load)
api/app/imports/router.py       (handlers + job store)
api/tests/                      (mirrors app/, fixtures built in tests)
frontend/Dockerfile  frontend/index.html
superset/Dockerfile  superset/superset_config.py  superset/bootstrap.sh
```

---

### Task 1: Compose skeleton and Postgres init

**Files:**
- Create: `docker-compose.yml`, `docker-compose.test.yml`, `.env.example`, `.gitignore`, `postgres/init/01-roles.sh`, `scripts/smoke.sh`

**Interfaces:**
- Produces: compose services `postgres`, `redis`; env vars `POSTGRES_PASSWORD`, `SPP_OWNER_PASSWORD`, `SPP_API_PASSWORD`, `SPP_READER_PASSWORD`, `SUPERSET_DB_PASSWORD`, `ADMIN_PASSWORD`, `SUPERSET_SECRET_KEY`; test override publishes Postgres on `localhost:55432`; `scripts/smoke.sh` exits non-zero on first failed check.

- [ ] **Step 1: Write the failing check.** `scripts/smoke.sh` section `postgres`: via `docker compose exec -T postgres psql`, assert roles `spp_owner`, `spp_api`, `spp_reader`, `superset` exist and databases `spp`, `superset_meta` exist.
- [ ] **Step 2: Run** `docker compose up -d postgres redis && scripts/smoke.sh postgres` — Expected: FAIL (roles missing).
- [ ] **Step 3: Implement** `postgres:16` with healthcheck (`pg_isready`), named volume, `.env` passwords, and `01-roles.sh` creating the four roles and two databases (`spp` owned by `spp_owner`, `superset_meta` by `superset`). `.gitignore` excludes `.env`. `docker-compose.test.yml` only adds `ports: ["55432:5432"]` to `postgres`.
- [ ] **Step 4: Run** `docker compose down -v && docker compose up -d postgres redis && scripts/smoke.sh postgres` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: compose skeleton with postgres roles and databases`.

### Task 2: Python API skeleton, config, migration runner, healthz

**Files:**
- Delete: all existing Go files under `api/` (`go.mod`, `go.sum`, `cmd/`, `internal/`, Go `migrations/`, old `Dockerfile`)
- Create: `api/requirements.txt`, `api/requirements-dev.txt`, `api/Dockerfile`, `api/app/__init__.py`, `api/app/config.py`, `api/app/db.py`, `api/app/main.py`, `api/app/migrations/0001_schemas_roles.sql`, `api/pytest.ini`
- Modify: `docker-compose.yml` (api service), `.env.example`
- Test: `api/tests/test_config.py`, `api/tests/test_db.py`, `api/tests/test_main.py`, `api/tests/conftest.py`

**Interfaces:**
- Produces:
  - `config.load_config(env: Mapping[str, str] | None = None) -> Config`; `Config` is a frozen dataclass `(database_url: str, migrate_database_url: str, admin_password: str, upload_dir: str)` from env `DATABASE_URL`, `MIGRATE_DATABASE_URL` (default: `DATABASE_URL`), `ADMIN_PASSWORD`, `UPLOAD_DIR` (default `/data/uploads`); raises `ConfigError` if `DATABASE_URL` or `ADMIN_PASSWORD` is empty.
  - `db.migrate(url: str) -> list[str]` — applies `app/migrations/*.sql` in filename order, each in its own transaction, records applied versions in `app.schema_migrations(version text primary key, applied_at timestamptz default now())` (the runner first runs `CREATE SCHEMA IF NOT EXISTS app` and the table), holds `pg_advisory_lock` while running, returns the versions applied this call (empty list when nothing is pending).
  - `db.open_pool(url: str) -> psycopg_pool.ConnectionPool`.
  - `main.create_app(cfg: Config | None = None) -> FastAPI` — app factory; startup runs `db.migrate(cfg.migrate_database_url)` then opens a pool on `cfg.database_url` (stored at `app.state.pool`, `app.state.cfg`); `GET /healthz` returns `200 {"status": "ok"}`.
  - `conftest.py` fixture `db_url` (env `TEST_DATABASE_URL`, skips the test when unset) and fixture `clean_db` that drops and recreates schemas `spp`, `app`, `usecase` (CASCADE) as `spp_owner` so each test starts empty.

- [ ] **Step 1: Write failing tests:** `test_load_requires_database_url`, `test_load_requires_admin_password`, `test_load_defaults` (`upload_dir == "/data/uploads"`, `migrate_database_url == database_url`), `test_healthz` (TestClient with a stub pool/migrate monkeypatched; 200 and `{"status": "ok"}`), `test_migrate_creates_schemas` (needs DB; after `migrate`, `information_schema.schemata` contains `spp`, `app`, `usecase`), `test_migrate_is_idempotent` (second call returns `[]`).
- [ ] **Step 2: Run** `cd api && python -m pytest -q` — Expected: FAIL (import errors).
- [ ] **Step 3: Implement** per the interfaces. Dependencies in `requirements.txt`: `fastapi`, `uvicorn[standard]`, `psycopg[binary,pool]`, `openpyxl`, `python-multipart`; dev: `pytest`, `httpx`. Migration `0001` creates schemas `spp`, `app`, `usecase` (IF NOT EXISTS) and grants: `spp_api` all DML on `spp` and `app` (existing tables and `ALTER DEFAULT PRIVILEGES` for tables created by the current role) plus `CREATE` on schema `usecase`; `spp_reader` `SELECT` on `spp` and `usecase` (existing + default privileges); `USAGE` on all three schemas for both roles. Dockerfile: `python:3.12-slim`, install requirements, `CMD uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8080`. In `docker-compose.yml` keep the existing `api` service but remove `ADDR`, keep env `DATABASE_URL`, `MIGRATE_DATABASE_URL`, `ADMIN_PASSWORD`, `UPLOAD_DIR`; add the missing ones to `.env.example` if absent.
- [ ] **Step 4: Run** `cd api && pip install -r requirements.txt -r requirements-dev.txt && TEST_DATABASE_URL=… python -m pytest -q` — Expected: PASS, output pristine.
- [ ] **Step 5: Commit** `feat: python api skeleton with config, migrations and healthz`.

### Task 3: Import job tables and admin auth

**Files:**
- Create: `api/app/migrations/0002_import_tables.sql`, `api/app/auth.py`
- Test: `api/tests/test_auth.py`, `api/tests/test_import_tables.py`

**Interfaces:**
- Produces: `auth.admin_auth(password: str) -> Callable` — a FastAPI dependency factory (HTTP Basic, username `admin`, `secrets.compare_digest`, `401` with `WWW-Authenticate: Basic` otherwise). Tables `app.import_job(id uuid primary key default gen_random_uuid(), filename text, status text check (status in ('uploaded','proposed','applied','failed')), proposal jsonb, created_at timestamptz default now())` and `app.import_issue(id bigserial primary key, job_id uuid references app.import_job(id), sheet text, "column" text, row int, message text)`.

- [ ] **Step 1: Write failing tests:** `test_rejects_missing_and_wrong` (no header → 401, wrong password → 401, wrong username → 401, all with `WWW-Authenticate`), `test_allows_correct`, `test_import_tables_exist` (needs DB; both tables exist after `migrate`; inserting an issue with unknown `job_id` raises a foreign-key error).
- [ ] **Step 2: Run** `python -m pytest api/tests/test_auth.py api/tests/test_import_tables.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** migration and dependency per the interfaces.
- [ ] **Step 4: Run** same with `TEST_DATABASE_URL` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: import job tables and admin auth`.

### Task 4: Workbook reader

**Files:**
- Create: `api/app/imports/__init__.py`, `api/app/imports/models.py`, `api/app/imports/workbook.py`
- Test: `api/tests/imports/test_workbook.py`

**Interfaces:**
- Produces in `models.py`: `Sheet(name: str, headers: list[str], rows: list[list[str]])`, `Workbook(sheets: list[Sheet])`, `Issue(sheet: str, column: str, row: int, message: str)` (plain dataclasses for Sheet/Workbook; `Issue` and every later proposal/result model is a pydantic model with `alias_generator=to_camel, populate_by_name=True` so JSON is lowerCamel).
- Produces in `workbook.py`: `read_workbook(f: IO[bytes]) -> tuple[Workbook, list[Issue]]` — row 1 is the header; every row padded/trimmed to `len(headers)`; trailing empty rows dropped; all values are strings; date/datetime cells are emitted as ISO-8601 (`2025-03-01`, or `2025-03-01T10:30:00` when a time part exists); header issues use `row == 1`.

- [ ] **Step 1: Write failing tests** (build fixture workbooks in the test with `openpyxl`):
  - `test_sheets_and_rows`: two sheets, headers and rows returned in order.
  - `test_date_cells_become_iso`: a `datetime.date(2025, 3, 1)` cell reads as `"2025-03-01"`.
  - `test_blank_and_duplicate_headers_are_issues`: headers `["a", "", "a", " "]` yield issues for columns 2, 3 (duplicate `a`) and 4 (blank) with `row == 1`; no column is silently merged or dropped.
  - `test_empty_sheet`: a sheet with no cells gives `headers == []` and `rows == []` and no exception.
- [ ] **Step 2: Run** `python -m pytest api/tests/imports/test_workbook.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `read_workbook` with `openpyxl.load_workbook(f, read_only=True, data_only=True)`.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: read xlsx workbooks with date normalisation and header checks`.

### Task 5: Type inference and column profiling

**Files:**
- Create: `api/app/imports/infer.py`
- Modify: `api/app/imports/models.py` (add `ColumnProfile`, `TableProfile`)
- Test: `api/tests/imports/test_infer.py`

**Interfaces:**
- Consumes: `Sheet`.
- Produces:
  - `ColumnProfile(name: str, sql_type: str, non_null: int, distinct: int, unique: bool, samples: list[str])` (JSON `sqlType`, `nonNull`, ...).
  - `TableProfile(name: str, columns: list[ColumnProfile], row_count: int)`.
  - `infer_column(name: str, values: list[str]) -> ColumnProfile` — empty strings count as null; `sql_type` is one of `boolean`, `integer`, `bigint`, `numeric(p,s)`, `date`, `timestamp`, `uuid`, `varchar(n)` (n = 50, 255 or 512, the smallest that fits the longest value), `text` (longer than 512, no non-null values, or no other type fits).
  - `profile_sheet(sheet: Sheet) -> TableProfile`.

- [ ] **Step 1: Write failing tests:** `test_boolean` (boolean only when every non-null value is `true`/`false`, case-insensitive; `["0","1"]` is `integer`), `test_integer_and_bigint` (`["1","20"]` → `integer`; `["3000000000"]` → `bigint`), `test_numeric` (`["12.50","3.1"]` → `numeric(4,2)`), `test_date_and_timestamp`, `test_uuid`, `test_mixed_falls_back_to_varchar` (`["12","abc"]` → `varchar(50)`), `test_leading_zeros_stay_text` (`["10110","01234"]` and `["0105536000011"]` → `varchar(50)`, never integer), `test_all_empty_is_text`, `test_unique` (`unique` true only when non-null values are all distinct), `test_profile_header_only_sheet` (zero rows: `row_count == 0`, every column `text`, no exception).
- [ ] **Step 2: Run** `python -m pytest api/tests/imports/test_infer.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** by trying candidate types in order boolean, uuid, integer/bigint, numeric, timestamp, date; a value with a leading zero (length > 1 and not a decimal like `0.5`) rules out the numeric types.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: column type inference and profiling`.

### Task 6: Key detection

**Files:**
- Create: `api/app/imports/keys.py`
- Modify: `api/app/imports/models.py` (add `ForeignKey`, `TableProposal`, `Proposal`)
- Test: `api/tests/imports/test_keys.py`

**Interfaces:**
- Consumes: `Workbook`, `TableProfile`.
- Produces (pydantic, lowerCamel JSON):
  - `ForeignKey(table: str, column: str, ref_table: str, ref_column: str, source: str, unmatched: int, accepted: bool)` (`source` is `"name"`, `"containment"` or `"dictionary"`).
  - `TableProposal(name: str, columns: list[ColumnProfile], primary_key: list[str])`.
  - `Proposal(tables: list[TableProposal], foreign_keys: list[ForeignKey], add_audit: bool = False, schema_only: bool = False)`.
  - `detect_keys(wb: Workbook, profiles: list[TableProfile], threshold: float = 0.99) -> Proposal` — PK: column named `<table>ID` (case-insensitive), else the single `unique` column with no nulls, else none. FK: a column that is not its table's PK and either has the same name as another table's PK column (`source="name"`), or whose non-null values are contained in another table's PK column at a rate >= `threshold` (`source="containment"`). `unmatched` counts non-null values missing from the referenced key; `accepted` is `unmatched == 0`.

- [ ] **Step 1: Write failing tests:** `test_primary_key_by_name` (`sppProject.sppProjectID`), `test_primary_key_by_sole_unique_column`, `test_foreign_key_by_name` (child column `sppProjectID` matches parent PK `sppProjectID`, `source == "name"`), `test_foreign_key_by_containment` (child column `sppProject` has a different name but all values are in `sppProject.sppProjectID`, `source == "containment"`), `test_foreign_key_by_containment_with_orphans` (2 of 100 values missing at threshold 0.95 → proposed with `unmatched == 2`, `accepted == False`), `test_no_keys_for_empty_table` (header-only sheet: no PK, no FKs, no exception).
- [ ] **Step 2: Run** `python -m pytest api/tests/imports/test_keys.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `detect_keys`; build each table's value set once.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: primary and foreign key detection`.

### Task 7: Dictionary mode and validation

**Files:**
- Create: `api/app/imports/dictionary.py`
- Test: `api/tests/imports/test_dictionary.py` (fixture workbooks built in the test)

**Interfaces:**
- Consumes: `Workbook`, `Issue`, `Proposal`, `TableProposal`, `ColumnProfile`, `ForeignKey`.
- Produces: `parse_dictionary(wb: Workbook) -> tuple[Proposal, list[Issue]]` — a sheet is a dictionary sheet when its headers begin `Table`, `Column`; each row defines one column from `Table`, `Column`, `Data type` and the last note column. Rows with an empty `Table` or `Column` are skipped. Types normalise to Postgres names (`character varying(255)` → `varchar(255)`, `timestamp with time zone` → `timestamptz`, `double precision`, `numeric(10,2)`, `uuid`, `text`, `boolean`, `date`, `integer`, `timestamp without time zone` → `timestamp`). A note matching `FK → table` or `FK → table.column` (the arrow is the character `→`) yields a `ForeignKey` with `source="dictionary"`; when no column is given it references that table's PK. PK of a table: the column named `<table>ID` (case-insensitive), else the first column if its name ends with `ID` and its type is `uuid`. The standard audit columns (`isDelete`, `version`, `createdDate`, `createdBy`, `createdName`, `createdEmployeeCode`, `createdPosition`, `createdDept`, the same five for `updated…` and `deleted…`, plus `updatedDate`, `deletedDate`) are removed from the tables and `add_audit` is set when they were present. The result has `schema_only=True`. Issues (in the returned list, `message` names the rule): a column whose name ends with `Date` and whose type is `boolean` or `integer`; a column whose name starts with `is` and whose type is not `boolean`; an unparseable type; an FK whose target table or column is not in the dictionary — that FK is kept with `accepted=False`.

- [ ] **Step 1: Write failing tests:** `test_types_and_primary_key`, `test_foreign_keys` (`FK → mtVoltageLevel` references that table's PK when the table exists), `test_audit_columns_folded_into_flag`, `test_validation_flags_boolean_date` (`documentDate` as `boolean` → one issue), `test_validation_flags_missing_fk_target` (issue, FK kept with `accepted == False`), `test_pk_fallback_first_uuid_id_column` (`addressID` uuid on table `sppProjectAddress`), `test_skips_rows_without_table_or_column`.
- [ ] **Step 2: Run** `python -m pytest api/tests/imports/test_dictionary.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** per the interface.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: dictionary-driven proposal with validation`.

### Task 8: DDL generation

**Files:**
- Create: `api/app/imports/ddl.py`
- Test: `api/tests/imports/test_ddl.py`

**Interfaces:**
- Consumes: `Proposal`.
- Produces: `generate_ddl(p: Proposal) -> tuple[list[str], list[str]]` returning `(statements, order)`: `statements` are `CREATE TABLE "spp"."<name>" (...)` strings in dependency order (quoted identifiers with `"` doubled, PK clause, accepted foreign keys inline as `REFERENCES "spp"."<ref>" ("<col>")`); `order` is the table names in the same order. Raises `DdlError("cycle detected: a -> b -> a")` on foreign-key cycles; a table referencing itself is allowed. When `add_audit` is true, append the standard audit columns: `isDelete boolean default false`, `version integer default 1`, `createdDate timestamptz`, `createdBy uuid`, `createdName varchar(255)`, `createdEmployeeCode varchar(50)`, `createdPosition varchar(255)`, `createdDept varchar(512)`, the same five for `updated…` (`updatedBy uuid`), and for `deleted…` with `deletedBy varchar(50)` and `deletedDate timestamptz`.

- [ ] **Step 1: Write failing tests:** `test_quotes_identifiers` (a name containing `"` is escaped by doubling), `test_orders_by_dependency`, `test_rejects_cycle`, `test_includes_only_accepted_foreign_keys`, `test_audit_columns`, `test_self_reference_allowed`.
- [ ] **Step 2: Run** `python -m pytest api/tests/imports/test_ddl.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** with Kahn's algorithm for ordering.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: generate ordered DDL from a proposal`.

### Task 9: Transactional apply

**Files:**
- Create: `api/app/imports/apply.py`
- Modify: `api/app/imports/models.py` (add `ApplyResult`)
- Test: `api/tests/imports/test_apply.py` (skips without `TEST_DATABASE_URL`)

**Interfaces:**
- Consumes: `Proposal`, `Workbook`, `generate_ddl`, `Issue`.
- Produces: `ApplyResult(created: list[str], skipped: list[str], issues: list[Issue])` (pydantic, lowerCamel) and `apply(pool: ConnectionPool, p: Proposal, wb: Workbook) -> ApplyResult` — one transaction; tables that already exist in schema `spp` go to `skipped` and their data is not loaded; the others are created and, unless `p.schema_only`, loaded in `order` with `COPY`; empty strings load as NULL; every value that cannot be converted to its column type adds `Issue(sheet, column, row, message)` where `row` is the 1-based sheet row including the header; if there are issues, or any database error (including a foreign-key violation), the whole transaction rolls back, nothing is created, and the function returns the issues (conversion problems) or raises (database errors).

- [ ] **Step 1: Write failing tests:** `test_creates_and_loads` (two tables with an FK; row counts match; the FK constraint exists in `information_schema`), `test_rolls_back_on_bad_value` (non-numeric text in an integer column → one issue with sheet, column and row; no table created), `test_rolls_back_on_orphan` (FK violation → exception; no tables), `test_schema_only_creates_without_rows`, `test_reimport_skips_existing_tables` (apply the same workbook twice: second call has all tables in `skipped`, `created == []`, row counts unchanged).
- [ ] **Step 2: Run** `TEST_DATABASE_URL=… python -m pytest api/tests/imports/test_apply.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `apply`; convert each string to a Python value by column type before `COPY` so failures carry row numbers.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: transactional apply with rollback report and skip on re-import`.

### Task 10: Import HTTP endpoints

**Files:**
- Create: `api/app/imports/router.py`, `api/app/migrations/0003_api_create_spp.sql`
- Modify: `api/app/main.py` (include the router under `admin_auth`)
- Test: `api/tests/imports/test_router.py` (skips without `TEST_DATABASE_URL`)

**Interfaces:**
- Consumes: Tasks 3–9.
- Produces: `router.build_router(admin: Callable) -> APIRouter` with
  - `POST /imports` (multipart field `file`, optional form field `mode=dictionary`): save to `<upload_dir>/<id>.xlsx`, create `app.import_job` (`uploaded`), build the proposal (dictionary mode → `parse_dictionary`; otherwise `profile_sheet` per sheet + `detect_keys` with threshold 0.99), store it in `proposal`, set `proposed`, respond `201 {id, proposal, issues}`.
  - `GET /imports/{id}` → `{id, status, proposal, issues, existingTables}` (`existingTables`: proposal table names that already exist in `spp`); `404` for an unknown id.
  - `PUT /imports/{id}/proposal` → replaces the stored proposal with the request body (a `Proposal`), `200`.
  - `POST /imports/{id}/apply` → runs `apply`; stores issues in `app.import_issue`; status `applied` or `failed`; `200 ApplyResult` or `422 {issues}`.
  - Migration `0003` gives `spp_api` `CREATE` on schema `spp` and sets `ALTER DEFAULT PRIVILEGES FOR ROLE spp_api IN SCHEMA spp GRANT SELECT ON TABLES TO spp_reader` (so Superset can read imported tables).

- [ ] **Step 1: Write failing tests** (TestClient, skip without DB): `test_upload_creates_proposal`, `test_put_proposal_persists`, `test_apply_happy_path` (tables appear in `spp`, status `applied`), `test_apply_failure_stores_issues` (`422`, rows in `app.import_issue`, status `failed`), `test_unknown_job_404`, `test_routes_require_auth` (no credentials → 401 on every route; `/healthz` stays open).
- [ ] **Step 2: Run** `TEST_DATABASE_URL=… python -m pytest api/tests/imports/test_router.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** the router; use `fastapi.UploadFile` and `run_in_threadpool`/sync `def` endpoints (psycopg pool is synchronous); keep the Basic auth dependency from Task 3 on every route.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: import API endpoints`.

### Task 11: Frontend import wizard and nginx

**Files:**
- Create: `frontend/index.html`, `frontend/Dockerfile`, `nginx/nginx.conf`
- Modify: `docker-compose.yml` (add `frontend`, `nginx`), `scripts/smoke.sh`

**Interfaces:**
- Consumes: Task 10 endpoints. Produces: nginx on port 80 routing `/` → `frontend`, `/api/` → `api:8080` (prefix stripped), `/superset/` → `superset:8088` (added in Task 12).

- [ ] **Step 1: Extend failing smoke checks** in `scripts/smoke.sh` section `web`: `curl -fs http://localhost/` contains `SPP`; `curl -fs http://localhost/api/healthz` returns `{"status":"ok"}`; `curl -s -o /dev/null -w '%{http_code}' http://localhost/api/imports/00000000-0000-0000-0000-000000000000` returns `401`.
- [ ] **Step 2: Run** `docker compose up -d --build && scripts/smoke.sh web` — Expected: FAIL.
- [ ] **Step 3: Implement** `index.html`: file picker (+ "dictionary mode" checkbox) → shows the proposal as an editable table (type input per column, PK, FK accept checkboxes with unmatched counts, "add audit columns") → Apply button → shows created, skipped and issues. Uses `fetch('/api/...')` with Basic auth entered once in the page. nginx sets `client_max_body_size 50m`.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: frontend import wizard behind nginx`.

### Task 12: Superset behind nginx

**Files:**
- Create: `superset/Dockerfile`, `superset/superset_config.py`, `superset/bootstrap.sh`
- Modify: `docker-compose.yml` (add `superset`), `nginx/nginx.conf`, `scripts/smoke.sh`

**Interfaces:**
- Produces: Superset reachable at `http://localhost/superset/`, metadata in `superset_meta` (role `superset`), cache/Celery broker on `redis`, and a database connection named `SPP` using `spp_reader`.

- [ ] **Step 1: Add failing smoke check** `superset`: `curl -fs http://localhost/superset/health` returns `OK`; the Superset REST API (logged in as admin) lists a database named `SPP`.
- [ ] **Step 2: Run** `scripts/smoke.sh superset` — Expected: FAIL.
- [ ] **Step 3: Implement** `superset_config.py` (`SUPERSET_APP_ROOT=/superset`, `SQLALCHEMY_DATABASE_URI` to `superset_meta`, Redis cache, `SECRET_KEY` from env). `bootstrap.sh`: `superset db upgrade`, create admin from env, `superset init`, then `superset set_database_uri -d SPP -u postgresql://spp_reader:…@postgres:5432/spp`. Compose waits for `postgres` healthy; nginx proxies `/superset/` with `X-Forwarded-*` headers.
- [ ] **Step 4: Run** `docker compose up -d --build && scripts/smoke.sh` — Expected: all sections PASS.
- [ ] **Step 5: Commit** `feat: superset behind nginx with read-only spp connection`.

### Task 13: End-to-end check and README

**Files:**
- Create: `README.md`
- Modify: `scripts/smoke.sh` (section `e2e`)

**Interfaces:**
- Consumes: everything. Produces: documented one-command start and a repeatable end-to-end check.

- [ ] **Step 1: Add failing e2e check.** `scripts/smoke.sh e2e` uploads `docs/data-dict-spp.xlsx` in dictionary mode, asserts the response lists the validation issues described in spec section 11 (at least the `documentDate` boolean in `sppDocProcExtendScod`), deletes the flagged tables from the proposal via `PUT`, applies, and asserts `spp.sppProject` exists with a foreign key to `sppBusinessPartner`; then queries it as `spp_reader` through Superset's SQL API and as `spp_reader` in `psql` asserting `INSERT` is denied.
- [ ] **Step 2: Run** `scripts/smoke.sh e2e` — Expected: FAIL until all earlier tasks are in place; then PASS.
- [ ] **Step 3: Write `README.md`:** prerequisites, `cp .env.example .env`, `docker compose up -d --build`, URLs (`/`, `/superset/`), how to run tests (`python -m pytest` in `api/` with `TEST_DATABASE_URL`, `scripts/smoke.sh`), and the known dictionary issues from spec section 11.
- [ ] **Step 4: Run** `docker compose down -v && docker compose up -d --build && scripts/smoke.sh` — Expected: all sections PASS.
- [ ] **Step 5: Commit** `docs: readme and end-to-end smoke check`.

### Task 14: Load data into existing tables

Closes the gap where a schema created from the dictionary (or an earlier import) could not receive rows. Run order for the remaining work: Tasks 2–10, 12, 14, 13.

**Files:**
- Modify: `api/app/imports/models.py` (add `rows_loaded: dict[str, int] = {}` to `ApplyResult`), `api/app/imports/apply.py`, `api/app/imports/router.py`, `frontend/index.html`, `scripts/smoke.sh`
- Test: `api/tests/imports/test_load_existing.py`, `api/tests/imports/test_router.py` (new cases)

**Interfaces:**
- Consumes: `Workbook`, `Issue`, `ApplyResult`, `ConnectionPool`.
- Produces: `load_existing(pool: ConnectionPool, wb: Workbook) -> ApplyResult` — for each sheet whose name equals an existing table in schema `spp` (case-sensitive): every header must be a column of that table (unknown header → `Issue`); values are converted by the table's real column types read from `information_schema`; sheets are loaded in foreign-key dependency order read from the catalog (not workbook order); a sheet with no matching table → `Issue("no table named …")`; all-or-nothing in one transaction (issues or a database error such as a primary-key or foreign-key violation roll everything back); result `rows_loaded` maps table name to row count.
- Router: `POST /loads` (multipart `file`) → `200 ApplyResult` or `422 {issues}`; records an `app.import_job` row (status `applied` or `failed`) and stores issues like the apply endpoint.
- Frontend: a second small section "Load data into existing tables" with a file picker and a Load button, showing `rowsLoaded` and issues.

- [ ] **Step 1: Write failing tests:** `test_loads_rows_into_existing_table`, `test_unknown_column_is_issue`, `test_unknown_sheet_is_issue`, `test_rolls_back_on_primary_key_conflict` (second load of the same workbook fails, row counts unchanged), `test_loads_in_dependency_order` (child sheet listed before parent still loads), `test_dictionary_then_data_flow` (apply a `schema_only` proposal, then `load_existing` with a data workbook; counts match), `test_loads_endpoint_422_on_issues`, `test_loads_requires_auth`.
- [ ] **Step 2: Run** `TEST_DATABASE_URL=… python -m pytest api/tests/imports/test_load_existing.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `load_existing`, the endpoint and the frontend section; reuse Task 9's value conversion (extract it to a shared function in `apply.py` rather than duplicating it).
- [ ] **Step 4: Run** `TEST_DATABASE_URL=… python -m pytest -q` in `api/` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: load data into existing tables`.

## Self-Review Notes

- **Spec coverage:** services/routing → Tasks 1, 2, 11, 12; import steps 1–6 → Tasks 4–10; roles → Tasks 1–3, 10; API (`/imports`, `/healthz`) → Tasks 2, 10; testing → every task plus Tasks 11–13; dictionary issues (spec 11) → Tasks 7, 13. `/schema` and `/usecases` endpoints, CRUD for `/projects` and `/partners`, and the view builder are deferred to the next plan (spec part 3); the `mt*` master tables arrive through the dictionary import once their definitions exist.
- **Added after approval:** backend switched from Go to Python (user request); Task 14 added to cover loading data into tables that already exist.
- **Type consistency:** `Sheet`, `Workbook`, `Issue` (Task 4), `ColumnProfile`/`TableProfile` (Task 5), `ForeignKey`/`TableProposal`/`Proposal` (Task 6) are reused unchanged in Tasks 7–10.
