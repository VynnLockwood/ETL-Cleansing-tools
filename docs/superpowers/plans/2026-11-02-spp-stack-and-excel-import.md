# SPP Stack and Excel Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the Docker stack (nginx, Postgres, Go API, frontend, Superset, Redis) and let the owner import an Excel workbook, review a proposed schema, and have the tables, keys and data created in Postgres.

**Architecture:** Go API (`net/http`, `pgx`, `excelize`, `golang-migrate`) owns migrations and the import pipeline: read workbook, profile columns, detect keys (or follow a dictionary), review, apply in one transaction. Superset reads the same database with a read-only role. nginx is the only public entry.

**Tech Stack:** Go 1.22, PostgreSQL 16, Apache Superset, Redis, nginx, Docker Compose, plain HTML/JS frontend.

**Spec:** `docs/superpowers/specs/2026-11-02-spp-service-design.md` (spec sections 3, 4, 6, 7, 8, 9, 10; use-case builder, spec section 5, is a later plan).

## Global Constraints

- PostgreSQL 16; databases `spp` and `superset_meta`; schemas `spp`, `app`, `usecase`.
- Roles: `spp_owner` (migrations, owns schemas), `spp_api` (read/write `spp` and `app`, can create views in `usecase`, no DDL on `spp` outside the import transaction), `spp_reader` (read-only `spp` and `usecase`, used by Superset).
- Only nginx publishes a port (80). Routes: `/` frontend, `/api/` Go, `/superset/` Superset (`SUPERSET_APP_ROOT`).
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
api/Dockerfile  api/go.mod  api/cmd/api/main.go
api/internal/config/config.go
api/internal/db/db.go                 (Open, Migrate)
api/migrations/0001_schemas_roles.up.sql  0002_import_tables.up.sql
api/internal/imports/workbook.go      (read xlsx)
api/internal/imports/infer.go         (types)
api/internal/imports/keys.go          (PK/FK detection)
api/internal/imports/dictionary.go    (dictionary mode + validation)
api/internal/imports/ddl.go           (proposal -> DDL)
api/internal/imports/apply.go         (transactional create + load)
api/internal/imports/http.go          (handlers + job store)
api/internal/httpx/auth.go            (admin auth middleware)
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

### Task 2: Go API skeleton, config, migrations runner, healthz

**Files:**
- Create: `api/go.mod`, `api/cmd/api/main.go`, `api/internal/config/config.go`, `api/internal/db/db.go`, `api/migrations/0001_schemas_roles.up.sql`, `api/Dockerfile`
- Test: `api/internal/config/config_test.go`, `api/internal/db/db_test.go`, `api/cmd/api/main_test.go`

**Interfaces:**
- Produces:
  - `config.Load() (Config, error)`; `Config{DatabaseURL, AdminPassword, UploadDir, Addr string}` from env `DATABASE_URL`, `ADMIN_PASSWORD`, `UPLOAD_DIR` (default `/data/uploads`), `ADDR` (default `:8080`); error if `DATABASE_URL` or `ADMIN_PASSWORD` empty.
  - `db.Open(ctx context.Context, url string) (*pgxpool.Pool, error)`.
  - `db.Migrate(url string) error` — runs embedded migrations as `spp_owner`.
  - `func newRouter(pool *pgxpool.Pool, cfg config.Config) http.Handler` in `main.go`; `GET /healthz` returns `200 {"status":"ok"}`.

- [ ] **Step 1: Write failing tests.** `TestLoadRequiresDatabaseURL` (empty env → error), `TestLoadDefaults` (`UploadDir == "/data/uploads"`, `Addr == ":8080"`), `TestHealthz` (httptest GET `/healthz` → 200 and body `{"status":"ok"}`), `TestMigrateCreatesSchemas` (skip when `TEST_DATABASE_URL` unset; after `Migrate`, `information_schema.schemata` contains `spp`, `app`, `usecase`).
- [ ] **Step 2: Run** `cd api && go test ./...` — Expected: FAIL (undefined).
- [ ] **Step 3: Implement** the signatures above. `0001` creates schemas `spp`, `app`, `usecase` and grants: `spp_api` ALL DML on `spp`/`app` (default privileges too) and CREATE on `usecase`; `spp_reader` SELECT on `spp` and `usecase` (default privileges too). Embed migrations with `//go:embed`. Dockerfile: multi-stage, static binary. Add `api` service to compose (env from `.env`, `UPLOAD_DIR` volume `uploads`, `DATABASE_URL` as `spp_owner` for migrate and `spp_api` for runtime — two env vars `MIGRATE_DATABASE_URL`, `DATABASE_URL`; extend `Config` accordingly).
- [ ] **Step 4: Run** `docker compose -f docker-compose.yml -f docker-compose.test.yml up -d postgres && cd api && TEST_DATABASE_URL=postgres://spp_owner:$SPP_OWNER_PASSWORD@localhost:55432/spp go test ./...` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: go api skeleton with config, migrations and healthz`.

### Task 3: Import job tables and admin auth

**Files:**
- Create: `api/migrations/0002_import_tables.up.sql`, `api/internal/httpx/auth.go`
- Test: `api/internal/httpx/auth_test.go`, `api/internal/db/import_tables_test.go`

**Interfaces:**
- Produces: `httpx.AdminAuth(password string) func(http.Handler) http.Handler` (HTTP Basic, username `admin`, constant-time compare, `401` with `WWW-Authenticate` otherwise). Tables `app.import_job(id uuid pk default gen_random_uuid(), filename text, status text check in ('uploaded','proposed','applied','failed'), proposal jsonb, created_at timestamptz default now())` and `app.import_issue(id bigserial pk, job_id uuid references app.import_job, sheet text, "column" text, row int, message text)`.

- [ ] **Step 1: Write failing tests.** `TestAdminAuthRejectsMissingAndWrong` (no header → 401, wrong password → 401), `TestAdminAuthAllowsCorrect` (→ next handler called), `TestImportTablesExist` (skip without `TEST_DATABASE_URL`; both tables exist, inserting an issue with unknown `job_id` fails).
- [ ] **Step 2: Run** `go test ./internal/httpx ./internal/db` — Expected: FAIL.
- [ ] **Step 3: Implement** migration and middleware per the interfaces.
- [ ] **Step 4: Run** same command with `TEST_DATABASE_URL` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: import job tables and admin auth`.

### Task 4: Workbook reader

**Files:**
- Create: `api/internal/imports/workbook.go`
- Test: `api/internal/imports/workbook_test.go`

**Interfaces:**
- Produces:
  - `type Sheet struct{ Name string; Headers []string; Rows [][]string }`, `type Workbook struct{ Sheets []Sheet }`, `type Issue struct{ Sheet, Column string; Row int; Message string }`.
  - `ReadWorkbook(r io.Reader) (Workbook, []Issue, error)` — row 1 is the header; every row padded/trimmed to `len(Headers)`; empty trailing rows dropped; cell values are strings; date-styled cells are emitted as ISO-8601 (`2006-01-02`, or `2006-01-02T15:04:05` when a time part exists).

- [ ] **Step 1: Write failing tests** (build fixture workbooks in the test with `excelize`):
  - `TestReadWorkbookSheetsAndRows`: two sheets, headers and rows returned in order.
  - `TestDateCellsBecomeISO`: a cell holding Excel date serial for 2025-03-01 with a date number format reads as `"2025-03-01"`.
  - `TestBlankHeaderIsIssue`: header `["a","","a"," "]` yields issues for columns 2, 3 (duplicate `a`) and 4 (blank) with `Row == 1`, and those columns are not silently merged.
  - `TestEmptySheet`: sheet with no cells returns `Headers == nil`, `Rows == nil`, no error.
- [ ] **Step 2: Run** `go test ./internal/imports -run 'ReadWorkbook|DateCells|BlankHeader|EmptySheet'` — Expected: FAIL.
- [ ] **Step 3: Implement** `ReadWorkbook` with `excelize.OpenReader`; use `GetRows` with `RawCellValue` and detect date cells via `GetCellStyle`/`GetStyle` number format to convert serials with `excelize.ExcelDateToTime`.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: read xlsx workbooks with date normalisation and header checks`.

### Task 5: Type inference and column profiling

**Files:**
- Create: `api/internal/imports/infer.go`
- Test: `api/internal/imports/infer_test.go`

**Interfaces:**
- Consumes: `Sheet` from Task 4.
- Produces:
  - `type ColumnProfile struct{ Name, SQLType string; NonNull, Distinct int; Unique bool; Samples []string }`.
  - `type TableProfile struct{ Name string; Columns []ColumnProfile; RowCount int }`.
  - `InferColumn(name string, values []string) ColumnProfile` — empty strings count as null; `SQLType` is one of `boolean`, `integer`, `bigint`, `numeric(p,s)`, `date`, `timestamp`, `uuid`, `varchar(n)` (n = longest value, rounded up to 50/255/512), `text` (longer than 512, or no non-null values or mixed types).
  - `ProfileSheet(s Sheet) TableProfile`.

- [ ] **Step 1: Write failing tests:** `TestInferBoolean` (`true/false/TRUE/0/1`-only column is not boolean unless values are `true/false`), `TestInferInteger` (`["1","20"]` → `integer`; `["3000000000"]` → `bigint`), `TestInferNumeric` (`["12.50","3.1"]` → `numeric(4,2)`), `TestInferDateAndTimestamp`, `TestInferUUID`, `TestMixedFallsBackToVarchar` (`["12","abc"]` → `varchar(50)`), `TestLeadingZerosStayText` (`["10110","01234"]` and a 13-digit `["0105536000011"]` → `varchar(50)`, never integer), `TestAllEmptyIsText`, `TestUnique` (`Unique` true only when non-null values are all distinct), `TestProfileHeaderOnlySheet` (zero rows: `RowCount == 0`, every column `text`, no panic).
- [ ] **Step 2: Run** `go test ./internal/imports -run 'Infer|Mixed|LeadingZeros|AllEmpty|Unique|Profile'` — Expected: FAIL.
- [ ] **Step 3: Implement** by testing candidate types in order boolean, uuid, integer/bigint, numeric, timestamp, date; any value with a leading zero (and length > 1, not a decimal) rules out numeric types.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: column type inference and profiling`.

### Task 6: Key detection

**Files:**
- Create: `api/internal/imports/keys.go`
- Test: `api/internal/imports/keys_test.go`

**Interfaces:**
- Consumes: `Workbook`, `TableProfile`.
- Produces:
  - `type ForeignKey struct{ Table, Column, RefTable, RefColumn, Source string; Unmatched int; Accepted bool }` (`Source` is `"name"`, `"containment"` or `"dictionary"`).
  - `type TableProposal struct{ Name string; Columns []ColumnProfile; PrimaryKey []string }`.
  - `type Proposal struct{ Tables []TableProposal; ForeignKeys []ForeignKey; AddAudit bool }`.
  - `DetectKeys(wb Workbook, profiles []TableProfile, threshold float64) Proposal` — PK: column named `<table>ID` (case-insensitive), else the single `Unique` column with no nulls, else none. FK: column (not the table's own PK) whose name equals another table's PK column name, or whose non-null values are contained in another table's PK column at a rate >= `threshold` (default use 0.99 from callers). `Unmatched` counts non-null values missing from the referenced key. `Accepted` is true when `Unmatched == 0`.

- [ ] **Step 1: Write failing tests:** `TestPrimaryKeyByName` (`sppProject.sppProjectID`), `TestPrimaryKeyBySoleUniqueColumn`, `TestForeignKeyByName` (child column `sppProjectID` matches parent PK `sppProjectID`, `Source == "name"`), `TestForeignKeyByContainment` (child column `sppProject` has a different name but all values are in `sppProject.sppProjectID`, `Source == "containment"`), `TestForeignKeyByContainmentWithOrphans` (2 of 100 values missing at threshold 0.95 → proposed with `Unmatched == 2`, `Accepted == false`), `TestNoKeysForEmptyTable` (header-only sheet yields a table with no PK and no FKs, no panic).
- [ ] **Step 2: Run** `go test ./internal/imports -run 'PrimaryKey|ForeignKey|NoKeys'` — Expected: FAIL.
- [ ] **Step 3: Implement** `DetectKeys`; build per-table value sets once.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: primary and foreign key detection`.

### Task 7: Dictionary mode and validation

**Files:**
- Create: `api/internal/imports/dictionary.go`
- Test: `api/internal/imports/dictionary_test.go`, fixture `api/internal/imports/testdata/dict_small.xlsx` (built in test, not committed binary)

**Interfaces:**
- Consumes: `Workbook`, `Issue`, `Proposal`, `TableProposal`, `ColumnProfile`, `ForeignKey`.
- Produces:
  - `ParseDictionary(wb Workbook) (Proposal, []Issue)` — a sheet is a dictionary sheet when its headers begin `Table`, `Column`; columns read from `Table`, `Column`, `Data type`, and the last note column. Types are normalised to Postgres names (`character varying(255)` → `varchar(255)`, `timestamp with time zone` → `timestamptz`, `double precision`, `numeric(10,2)`, `uuid`, `text`, `boolean`, `date`, `integer`). A note matching `FK → table` or `FK → table.column` yields a `ForeignKey` with `Source: "dictionary"`; when no column is given it references the target table's PK. A column named `<table>ID` is the PK. The standard audit columns found in the dictionary are removed from `Tables` and `AddAudit` is set true.
  - Validation issues (in the returned `[]Issue`): column name ending `Date` with type `boolean` or `integer`; name starting `is` with a non-boolean type; unparseable type; FK target table or column not in the dictionary. Issue `Message` names the rule.

- [ ] **Step 1: Write failing tests:** `TestParseDictionaryTypesAndPK`, `TestParseDictionaryForeignKeys` (`mtVoltageLevel` note `FK → mtVoltageLevel` references that table's PK), `TestAuditColumnsFoldedIntoFlag`, `TestValidationFlagsBooleanDate` (`documentDate` as `boolean` → one issue), `TestValidationFlagsMissingFKTarget`.
- [ ] **Step 2: Run** `go test ./internal/imports -run 'Dictionary|Validation|Audit'` — Expected: FAIL.
- [ ] **Step 3: Implement** per the interface. Dictionary sheets have a shape identical to `docs/data-dict-spp.xlsx`; the real file is used in Task 13's end-to-end check.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: dictionary-driven proposal with validation`.

### Task 8: DDL generation

**Files:**
- Create: `api/internal/imports/ddl.go`
- Test: `api/internal/imports/ddl_test.go`

**Interfaces:**
- Consumes: `Proposal`.
- Produces: `GenerateDDL(p Proposal) (tables []string, order []string, err error)` — `tables` are `CREATE TABLE "spp"."<name>"` statements (quoted identifiers, PK clause, accepted FK constraints inline as `REFERENCES`), `order` is the table names topologically sorted so referenced tables come first; error `cycle detected: a -> b -> a` on FK cycles. When `AddAudit` is true, append the standard audit columns (`isDelete boolean default false`, `version integer default 1`, `createdDate`, `createdBy uuid`, `createdName`, `createdEmployeeCode`, `createdPosition`, `createdDept`, `updatedDate`, `updatedBy uuid`, `updatedName`, `updatedEmployeeCode`, `updatedPosition`, `updatedDept`, `deletedDate`, `deletedBy`, `deletedName`, `deletedEmployeeCode`, `deletedPosition`, `deletedDept`, with the types used in `sppProject` in the data dictionary).

- [ ] **Step 1: Write failing tests:** `TestDDLQuotesIdentifiers` (name containing `"` is escaped by doubling), `TestDDLOrdersByDependency`, `TestDDLRejectsCycle`, `TestDDLIncludesOnlyAcceptedForeignKeys`, `TestDDLAuditColumns`.
- [ ] **Step 2: Run** `go test ./internal/imports -run DDL` — Expected: FAIL.
- [ ] **Step 3: Implement** with Kahn's algorithm for ordering.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: generate ordered DDL from a proposal`.

### Task 9: Transactional apply

**Files:**
- Create: `api/internal/imports/apply.go`
- Test: `api/internal/imports/apply_test.go` (skips without `TEST_DATABASE_URL`)

**Interfaces:**
- Consumes: `Proposal`, `Workbook`, `GenerateDDL`, `Issue`.
- Produces: `type ApplyResult struct{ Created, Skipped []string; Issues []Issue }` and `Apply(ctx context.Context, pool *pgxpool.Pool, p Proposal, wb Workbook) (ApplyResult, error)` — one transaction; tables already existing in schema `spp` go to `Skipped` and their data is not loaded; other tables are created and loaded in `order` with `pgx.CopyFrom`; empty strings load as NULL; any value that cannot be converted adds an `Issue{Sheet, Column, Row, Message}`; if there are issues or any error, roll back everything and return them.

- [ ] **Step 1: Write failing tests:** `TestApplyCreatesAndLoads` (two tables with an FK; row counts match; FK constraint exists), `TestApplyRollsBackOnBadValue` (non-numeric in an integer column → issue with sheet, column, row; no table created), `TestApplyRollsBackOnOrphan` (FK violation → error, no tables), `TestReimportSkipsExistingTables` (apply the same workbook twice: second returns `Skipped` with all tables, `Created` empty, row counts unchanged).
- [ ] **Step 2: Run** `TEST_DATABASE_URL=… go test ./internal/imports -run Apply` — Expected: FAIL.
- [ ] **Step 3: Implement** `Apply`; parse each string to the column's Go type before `CopyFrom` so conversion failures carry row numbers (row = 1-based sheet row including header).
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: transactional apply with rollback report and skip on re-import`.

### Task 10: Import HTTP endpoints

**Files:**
- Create: `api/internal/imports/http.go`
- Modify: `api/cmd/api/main.go` (mount routes under admin auth)
- Test: `api/internal/imports/http_test.go`

**Interfaces:**
- Consumes: Tasks 3–9.
- Produces: `NewHandler(pool *pgxpool.Pool, uploadDir string) http.Handler` with routes
  - `POST /imports` (multipart field `file`, optional `mode=dictionary`): save to `uploadDir/<id>.xlsx`, create `app.import_job` (`uploaded`), build the proposal (dictionary mode → `ParseDictionary`; else `ProfileSheet` + `DetectKeys` threshold 0.99), store it in `proposal`, set `proposed`, respond `201 {id, proposal, issues}`.
  - `GET /imports/{id}` → `{id, status, proposal, issues, existingTables}`.
  - `PUT /imports/{id}/proposal` → replace the stored proposal (types, PK, FK `Accepted`, `AddAudit`).
  - `POST /imports/{id}/apply` → run `Apply`; store issues in `app.import_issue`; status `applied` or `failed`; `200 ApplyResult` or `422` with issues.

- [ ] **Step 1: Write failing tests** (httptest, skip without DB): `TestUploadCreatesProposal`, `TestPutProposalPersists`, `TestApplyHappyPath` (tables appear in `spp`), `TestApplyFailureStoresIssues` (`422`, rows in `app.import_issue`, status `failed`), `TestRoutesRequireAuth` (no credentials → 401 on every route).
- [ ] **Step 2: Run** `TEST_DATABASE_URL=… go test ./internal/imports -run 'Upload|PutProposal|ApplyHappy|ApplyFailure|RoutesRequireAuth'` — Expected: FAIL.
- [ ] **Step 3: Implement** handlers using Go 1.22 `ServeMux` patterns; mount under `/imports` with `httpx.AdminAuth(cfg.AdminPassword)`; the runtime pool uses `spp_api`, and `Apply` creates tables with it (granted CREATE on schema `spp` in a migration `0003_api_create_spp.up.sql`, revoked outside `Apply` by running DDL only inside the transaction).
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
- [ ] **Step 3: Write `README.md`:** prerequisites, `cp .env.example .env`, `docker compose up -d --build`, URLs (`/`, `/superset/`), how to run tests (`go test ./...` with `TEST_DATABASE_URL`, `scripts/smoke.sh`), and the known dictionary issues from spec section 11.
- [ ] **Step 4: Run** `docker compose down -v && docker compose up -d --build && scripts/smoke.sh` — Expected: all sections PASS.
- [ ] **Step 5: Commit** `docs: readme and end-to-end smoke check`.

## Self-Review Notes

- **Spec coverage:** services/routing → Tasks 1, 2, 11, 12; import steps 1–6 → Tasks 4–10; roles → Tasks 1–3, 10; API (`/imports`, `/healthz`) → Tasks 2, 10; testing → every task plus Tasks 11–13; dictionary issues (spec 11) → Tasks 7, 13. `/schema` and `/usecases` endpoints, CRUD for `/projects` and `/partners`, and the view builder are deferred to the next plan (spec part 3); the `mt*` master tables arrive through the dictionary import once their definitions exist.
- **Type consistency:** `Sheet`, `Workbook`, `Issue` (Task 4), `ColumnProfile`/`TableProfile` (Task 5), `ForeignKey`/`TableProposal`/`Proposal` (Task 6) are reused unchanged in Tasks 7–10.
