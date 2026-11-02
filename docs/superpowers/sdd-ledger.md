# SDD ledger — plan: docs/superpowers/plans/2026-11-02-spp-stack-and-excel-import.md

Spec: docs/superpowers/specs/2026-11-02-spp-service-design.md (reachable). Branch: design/spp-service-spec.

## Preflight scan
| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1 -> T2 | env vars, roles, compose services | consistent; T2 adds `api` service + MIGRATE_DATABASE_URL not in T1 .env.example |
| T2 -> T3 | db.Migrate, migrations 0001 then 0002 | consistent |
| T2 Config | Config lists 4 fields, step 3 adds MigrateDatabaseURL | internal mismatch |
| T3 -> T10 | AdminAuth, import_job/import_issue | consistent |
| T4 -> T5 -> T6 | Sheet/Issue -> ColumnProfile/TableProfile -> Proposal | no JSON tags specified, but T10 stores proposal as jsonb and T11 reads it |
| T6 -> T7 -> T8 -> T9 | Proposal reused | Dictionary mode needs a schema-only flag (dictionary sheets hold column defs, not data) |
| T7 | dictionary PK rule `<table>ID` | real dictionary PKs are addressID, caseID, businessPartnerID, docID |
| T7 | FK notes to mt* tables | mt* tables are not in the dictionary; FK targets missing |
| T8 -> T9 | GenerateDDL order | consistent |
| T9 -> T10 | ApplyResult | consistent |
| T10 | grants CREATE on spp to spp_api, "revoked outside Apply" | contradictory: grants are persistent |
| T10 -> T13 | tables created by spp_api, T13 queries as spp_reader | default privileges in 0001 cover only spp_owner-created tables |
| T5 | TestInferBoolean wording | ambiguous |
| T11 -> T10, T12 -> T11 | nginx routes | consistent |
| T1..T13 | each task's tests vs code vs files | otherwise self-consistent |

## Rulings
Ruling: Config.MigrateDatabaseURL is optional and defaults to DatabaseURL — keeps tests simple — cost if wrong: none material.
Ruling: spp_api keeps a persistent CREATE on schema spp (migration 0003); "no DDL outside import" is enforced by code path (only Apply issues DDL), not privilege — a revocable grant is unimplementable — cost if wrong: a bug in API code could create tables.
Ruling: migration 0003 also sets ALTER DEFAULT PRIVILEGES FOR ROLE spp_api IN SCHEMA spp GRANT SELECT ON TABLES TO spp_reader — otherwise Superset cannot read imported tables — cost if wrong: dashboards see nothing.
Ruling: all Proposal-related structs (ColumnProfile, TableProfile, ForeignKey, TableProposal, Proposal) get lowerCamel json tags (e.g. sqlType, foreignKeys, addAudit, primaryKey, unmatched, accepted, source) — needed for jsonb storage and the frontend — cost if wrong: rename later.
Ruling: Proposal gains `SchemaOnly bool` (json schemaOnly); ParseDictionary sets it; Apply creates tables but loads no rows when set — dictionary sheets contain definitions, not data — cost if wrong: dictionary rows would be loaded as data.
Ruling: dictionary PK rule is `<table>ID`, else first column if its name ends with ID and its type is uuid; rows with empty Table or Column are skipped — matches real docs/data-dict-spp.xlsx — cost if wrong: tables without PK.
Ruling: dictionary FK whose target table is absent is reported as an Issue and the FK is kept with Accepted=false (not created) — mt* tables are undefined — cost if wrong: FKs to masters never created until mt* defined.
Ruling: boolean inference iff every non-null value, case-insensitive, is true or false; 0/1 columns infer integer — cost if wrong: flag columns stored as integer.
Ruling: KNOWN GAP, not fixed in this plan: Apply skips already-existing tables and loads no data into them (spec: new tables only). So "dictionary first, data workbook second" will create the schema but not load rows. Needs a follow-up (load into existing tables) — surface to user at the end — cost if wrong: owner cannot load data after schema creation.

## Progress
Ruling: no Docker daemon in this sandbox (CLI only). Tests that need Postgres run against a local PostgreSQL 16 on /tmp/pgtest socket, port 55432, superuser postgres (trust auth); implementers create roles/databases by running postgres/init/01-roles.sh against it. Compose/nginx/Superset smoke checks cannot run here; those tasks are verified statically (docker compose config, shell -n, nginx -t if available) and the real `docker compose up` + scripts/smoke.sh run is left to the user — cost if wrong: stack wiring bugs surface only on first real run.
Task 1: minor (deferred): init script passes passwords via psql -v (visible in ps inside container)
Task 1: minor (deferred): role password changes in .env not applied to existing roles — document in README (Task 13)
Task 1: fix round 1/5 (1 addressed pending re-review — healthcheck/smoke init race; commits 8b18e3d..cf2adc6)
Task 1: minor (deferred): smoke.sh wait still races during cold-start init if run without `docker compose up --wait` — document `up -d --wait` in README (Task 13)
Task 1: complete (commits bf597d4..cf2adc6, review clean after 1 fix round)
Task 2: BLOCKED — go get/mod tidy cannot fetch pgx v5 / golang-migrate: proxy.golang.org answers 403 to CONNECT (egress policy denial; DNS direct also fails). Implementer left uncommitted files under api/ (go.mod without requires, config, db, cmd/api, migration 0001, Dockerfile). Not retrying or routing around the policy; asked the user. Tasks 3-10 (all Go with deps) are blocked on the same host. Test DB roles: spp_owner=ownerpw, spp_api=apipw (local test server only).
