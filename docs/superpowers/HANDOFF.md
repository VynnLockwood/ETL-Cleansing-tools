# Handoff: SPP stack and Excel import

Date: 2026-11-02. Branch: `design/spp-service-spec`.

## Where things stand

- Spec: `docs/superpowers/specs/2026-11-02-spp-service-design.md` (approved).
- Plan: `docs/superpowers/plans/2026-11-02-spp-stack-and-excel-import.md` (approved, 13 tasks).
- Task 1 (compose skeleton, Postgres roles/databases, `scripts/smoke.sh postgres`): done, reviewed, committed.
- Task 2 (Go API skeleton): drafted but **not reviewed and not verified**. It is committed as a separate `wip:` commit. It has no `go.sum` and no dependencies because the sandbox could not download Go modules. Treat it as a starting point: run `go mod tidy`, run the tests (RED/GREEN), finish the missing parts, then review.
  - Still missing from Task 2: `api` service in `docker-compose.yml`, `.env.example` additions (`MIGRATE_DATABASE_URL`, `DATABASE_URL`, `ADMIN_PASSWORD`), `docker compose config` check, integration test run.
- Tasks 3 to 13: not started.

## Why it stopped

The sandbox network policy blocked `proxy.golang.org` and `sum.golang.org`, so pgx v5 and golang-migrate could not be downloaded, and there was no Docker daemon. Run the remaining work somewhere with Go module access and Docker.

## Rulings made while preparing execution (the plan text does not say these; apply them)

1. `Config.MigrateDatabaseURL` (env `MIGRATE_DATABASE_URL`) is optional and defaults to `DatabaseURL`.
2. Migration 0003 gives `spp_api` a persistent `CREATE` on schema `spp`. "No DDL outside the import transaction" is enforced by code path (only `Apply` issues DDL), not by privilege.
3. Migration 0003 also sets `ALTER DEFAULT PRIVILEGES FOR ROLE spp_api IN SCHEMA spp GRANT SELECT ON TABLES TO spp_reader`, otherwise Superset cannot read imported tables.
4. All proposal structs (`ColumnProfile`, `TableProfile`, `ForeignKey`, `TableProposal`, `Proposal`) get lowerCamel JSON tags (`sqlType`, `foreignKeys`, `addAudit`, `primaryKey`, `unmatched`, `accepted`, `source`).
5. `Proposal` gains `SchemaOnly bool` (`schemaOnly`). `ParseDictionary` sets it; `Apply` creates tables but loads no rows when it is set (dictionary sheets hold column definitions, not data).
6. Dictionary PK rule: column named `<table>ID`, else the first column if its name ends in `ID` and its type is uuid. Rows with empty Table or Column are skipped.
7. A dictionary FK whose target table is absent from the dictionary (the `mt*` masters) is reported as an Issue and kept with `Accepted=false`.
8. Boolean inference: a column is boolean only if every non-null value is `true` or `false` (case-insensitive). Columns of `0`/`1` infer integer.
9. **Known gap, not solved:** `Apply` skips already-existing tables and loads no data into them (spec: new tables only). So "import the dictionary first, then a data workbook" creates the schema but does not load rows. A follow-up is needed to load data into existing tables.

## Deferred minor review findings

- `postgres/init/01-roles.sh` passes passwords through `psql -v` (visible in `ps` inside the container).
- Role password changes in `.env` are not applied to existing roles; document in the README (Task 13).
- `scripts/smoke.sh postgres` can still race during a cold start unless the stack is started with `docker compose up -d --wait`; document in the README (Task 13).

## Not verified anywhere

`docker compose up`, nginx routing, Superset startup and every `scripts/smoke.sh` section other than `postgres` were never run (no Docker daemon).

## How the work was run

Subagent-driven development: one implementer per task, then a spec and quality review, then a scoped re-review after any fixes. A copy of the progress ledger is saved next to this file as `sdd-ledger.md`.
