# SPP Database Service: Design

Date: 2026-11-02
Status: Approved; implemented (see docs/superpowers/HANDOFF.md)

## 1. Purpose

The owner has SPP data in Excel and does not know how to structure it in a database. The service lets them:

1. Import an Excel workbook (one sheet per table) and have the service create the database structure and relationships.
2. Enter and edit data through an API and simple frontend.
3. Pick a use case (a set of fields), see an ERD limited to that use case, get the SQL for it, and chart it in Apache Superset on live data.

Success criteria:

- Importing a workbook shaped like `docs/data-dict-spp.xlsx` produces tables, keys and foreign keys after the owner confirms a proposal.
- Data saved through the API is visible in Superset on the next query, with no ETL step.
- For a saved use case, the service returns an ERD (Mermaid), the SQL, and a published view usable as a Superset dataset.

## 2. Scope and decomposition

Three parts, built in this order. Each part has its own plan.

1. Stack: Docker Compose with nginx, Postgres, Python API (FastAPI), simple frontend, Superset, Redis.
2. Excel import and schema creation.
3. Use-case ERD and view builder.

This spec covers parts 1 and 2 in detail and part 3 at design level. First version covers the core SPP tables: `sppProject`, `sppProjectAddition`, `sppProjectConnection`, `sppProjectAddress`, `sppBusinessPartner`, `sppBusinessPartnerAddress`, `sppBusinessPartnerProject`, plus the `mt*` master tables they reference. The SCOD extension and Connect Generator case tables come in later iterations.

Out of scope for now: user accounts and per-user permissions, embedding Superset in the frontend, aggregating one-to-many children in use cases, schema design suggestions, destructive schema changes on re-import.

## 3. Services and routing

Docker Compose, one internal network, only nginx publishes a port (80).

| Service | Role | Notes |
|---|---|---|
| nginx | Reverse proxy | `/` to frontend, `/api/` to the Python API, `/superset/` to Superset |
| frontend | Simple static UI | Import wizard, use-case builder, Mermaid ERD, links to Superset |
| api | Python backend (FastAPI, REST, JSON) | Runs migrations at startup |
| postgres | PostgreSQL 16 | Databases `spp` and `superset_meta`, created by init script |
| superset | Apache Superset | Served under `/superset` via `SUPERSET_APP_ROOT`; reads `spp` read-only |
| redis | Superset cache (no Celery in v1) | Not used by the API |

Startup order: postgres healthy, then api (migrates) and superset (`db upgrade`, admin user, `init`), then nginx. Secrets live in `.env` (not in git), with a committed `.env.example`. Postgres and Superset use named volumes.

Live data: Superset queries Postgres directly. SPP datasets use no cache or a very short one, and dashboards use auto-refresh.

## 4. Excel import and schema creation

Input: an `.xlsx` where each sheet is a table and row 1 is the header.

1. Upload via `POST /api/imports`. Each sheet is a candidate table.
2. Profile each column: non-null count, distinct count, samples. Infer one type: boolean, integer, numeric(p,s), date, timestamp, uuid, `varchar(n)` or text.
3. Propose keys.
   - Primary key: a column named `<table>ID`, or a single column that is unique and never empty.
   - Foreign key: a column whose name matches another table's key, or whose values all appear in another table's key column (threshold configurable). Each suggestion shows the count of unmatched values.
   - Dictionary shortcut: if the workbook contains a dictionary sheet (Table, Column, Data type, notes such as "FK to sppProject.sppProjectID"), use its declared types and foreign keys instead of inferring. Flag impossible combinations (for example a `boolean` column named `documentDate`).
4. Review: the UI shows the proposal as an editable list (types, keys, accept or reject each foreign key, option to add the standard audit columns). Nothing touches the database until Apply.
5. Apply: in one transaction, create the tables in schema `spp` and load data in dependency order. Any value that cannot be converted rolls back the whole import and produces a report (sheet, row, column).
6. Re-import (version 1): create new tables only. For existing tables show a diff, make no destructive changes. Each import is recorded in `app.import_job`, problems in `app.import_issue`.

## 5. Use-case ERD and view builder

1. The service reads tables, columns and foreign keys from the Postgres catalog, so the ERD matches the real database. Migrations and the import must create real FK constraints, including those on `mt*` code columns.
2. A use case is a name, a list of `table.column` fields and an optional root table, stored in schema `app`.
3. Join resolution: treat the FK graph as undirected and find the shortest path from the root to each chosen table. Tables on the path are included automatically. If several paths exist, the API returns the options and the choice is saved with the use case. Child tables join with `LEFT JOIN`. The UI warns that one-to-many children multiply rows.
4. Outputs:
   - ERD: Mermaid `erDiagram` text for the tables on the join tree, chosen columns marked.
   - SQL: a generated `SELECT`. Identifiers come only from the catalog, never from free text.
   - Publish: create a plain (non-materialized) view `usecase.uc_<slug>`, grant `SELECT` to the Superset role, register or refresh a Superset dataset through the Superset REST API.
5. Defaults: audit columns hidden, view filters `isDelete = false`; both can be switched off per use case.

## 6. Database roles

All in database `spp`.

| Role | Access |
|---|---|
| `spp_owner` | Runs migrations; owns schemas `spp`, `app`, `usecase` |
| `spp_api` | Read and write on `spp` and `app`; can create views in `usecase`; no DDL on `spp` outside the import transaction |
| `spp_reader` | Read-only on `spp` and `usecase`; used by Superset |

Superset metadata lives in `superset_meta`, owned by its own role.

## 7. API

JSON under `/api`.

- `POST /imports`, `GET /imports/{id}`, `PUT /imports/{id}/proposal`, `POST /imports/{id}/apply`
- `GET /schema`
- `GET/POST /usecases`, `POST /usecases/{id}/resolve`, `POST /usecases/{id}/publish`
- CRUD for core tables after the import creates the schema: `/projects`, `/partners`
- `GET /healthz`

Authentication in version 1: one admin login (shared secret from `.env`) for the API; Superset uses its own admin account. Assumes an internal network.

## 8. Repo layout

```
docker-compose.yml   .env.example   README.md
nginx/        nginx.conf
postgres/     init/            (create roles and databases)
api/          app/{config,db,main,auth}.py, app/migrations/*.sql,
              app/imports/{models,workbook,infer,keys,dictionary,ddl,apply,load,router}.py,
              tests/, Dockerfile
frontend/     index.html and small JS, Dockerfile
superset/     superset_config.py, bootstrap.sh, Dockerfile
docs/         superpowers/specs/, data-dict-spp.xlsx
```

## 9. Error handling

- Import failures roll back and produce a report.
- View publish or Superset sync failures return a clear message and leave the use case saved, so the owner can retry.

## 10. Testing

- Python unit tests (pytest): type inference, key and foreign-key detection, join-path search, using fixture workbooks. The SPP data dictionary with its known misaligned types is a negative test.
- Integration tests: a throwaway Postgres container runs import, apply, publish a view, and checks the returned data.
- Smoke test: after `docker compose up`, `/healthz`, `/superset/health` and the frontend respond through nginx.

## 11. Known issues in the current data dictionary

To resolve before or during the first import:

- Data types are misaligned with column names in `sppCaseConnectGenerator`, `sppCaseConnectGenInspectGen`, `sppCaseConnectGenConnection`, `sppDocProcExtendScod` and `sppDocProcExtendScodDetail`. These are outside the first-version scope but will fail dictionary validation.
- `sppProjectAddress`: `updatedBy`/`updatedDate` and related audit columns have shifted types.
- Unit mismatches: `genContractCapacityW` (described as MW), `distanceToConnectionM` (km), `bessCapacityWh` (MW-hr).
- `peaConnectionPointA` and `peaConnectionPointB` both map to source column `AU`.
- `sppProjectAddress` carries a note that a PEA address was put in the plant address column by mistake.
- The `mt*` master tables referenced by foreign keys ARE defined on the dictionary's `Master` sheet, except `mtVoltageLevel` and `mtZone`. The foreign keys to those two are left unaccepted in the proposal, and their structure and values are still needed.
