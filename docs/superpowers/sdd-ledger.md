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
## Resumed on master (c4d949d from Hermes agent: api compose service, go 1.25.11, go.sum). This session still cannot reach proxy.golang.org nor install Go 1.25.11, so only non-Go Tasks 11 and 12 are done here, on branch feat/frontend-nginx-superset (off master). Tasks 2-10 stay with the other agent.
Ruling: Task 11 nginx.conf contains only `/` and `/api/` locations; the `/superset/` location is added in Task 12 (nginx fails to start when an upstream host does not resolve) — cost if wrong: none.
Ruling: JSON contract for the frontend: Proposal fields lowerCamel (tables[].name, tables[].columns[].name/sqlType, tables[].primaryKey, foreignKeys[].table/column/refTable/refColumn/source/unmatched/accepted, addAudit, schemaOnly); ApplyResult and Issue also lowerCamel (created, skipped, issues; sheet, column, row, message). POST /imports 201 {id, proposal, issues}; GET /imports/{id} {id, status, proposal, issues, existingTables}; PUT /imports/{id}/proposal body = proposal; POST /imports/{id}/apply 200 ApplyResult or 422 {issues}. Basic auth user `admin`. — cost if wrong: frontend/API field mismatch to fix on one side.
Ruling: Superset image pinned via ARG SUPERSET_VERSION default 5.0.0, unverifiable offline — cost if wrong: image tag fix at first build.
Ruling: Tasks 11/12 verified statically only (docker compose config, bash -n, py_compile, HTML parse); nginx binary and Docker are absent — real run is the user's.
Task 11: minor (deferred): btoa() throws on non-Latin1 passwords (index.html:127); Upload/Apply not disabled in flight; `i.row || ""` hides row 0; existingTables unused; bare /api not routed
Task 11: cross-task check for API owner: Basic auth user `admin` accepted; /healthz unauthenticated returning exactly {"status":"ok"}; created/skipped are string arrays
Ruling: Task 12 also adds `proxy_read_timeout 300s;` to the /api/ location (Apply on big workbooks can exceed nginx's 60s default) — cost if wrong: none.
Task 11: complete (commits c4d949d..2ffd20c, review clean)
## Backend switched Go -> Python at user request (spec + plan edited; Go files to be deleted in Task 2). User asked me to do ALL remaining tasks (2-10, 12, 13) plus Task 14 (load data into existing tables, closes the known gap).
Ruling: Python stack = FastAPI + psycopg 3 (+pool) + openpyxl + pytest, plain SQL migrations with a tiny runner (tracking table app.schema_migrations, advisory lock) — Alembic/ORM would be heavier than the plan needs — cost if wrong: swap migration runner later.
Ruling: all proposal/result models are pydantic with camelCase aliases, matching the earlier JSON-contract ruling.
Ruling: run order Tasks 2-10, 12, 14, 13. Tasks 1 and 11 done. Work happens on branch feat/frontend-nginx-superset (off master c4d949d); master has Hermes' Go commit which the user must reconcile (Go files are deleted here) — cost if wrong: merge conflict in api/.
Ruling: Task 14 `POST /loads` appends rows into existing tables (no dedupe); primary keys make a repeated load fail and roll back — cost if wrong: tables without PK accept duplicates.
Ruling: spp_owner cannot ALTER DEFAULT PRIVILEGES FOR ROLE spp_api (Task 2 finding). So Task 10's migration 0003 only grants CREATE on schema spp to spp_api (no default-privilege clause), and Task 9 Apply issues `GRANT SELECT ON "spp"."<table>" TO spp_reader` for every table it creates (spp_api owns them) — cost if wrong: Superset cannot read a table until granted manually. Same rule for usecase views later.
Task 2: implementer wrote tests and code together (no separate RED run) — reviewer to judge test strength.
Task 2: minor (deferred): Dockerfile runs as root (add USER + uploads dir ownership); requirements unpinned (pin at Task 13); test_main does not assert migrate-before-pool order/URL; no test asserts 0001 grants (add a role-grant test in Task 10 or 13); app.state.cfg assigned twice
Task 2: complete (commits 3139371..9198a14, review clean)
Task 3: minor (deferred): test_db asserts only applied[0] (use sorted migration files on disk); admin_auth("") allows empty password (config already rejects empty ADMIN_PASSWORD); redundant GRANTs in 0002; import_job.status nullable; cosmetic try/finally in test
Task 3: complete (commits 9198a14..8af8d40, review clean)
Task 4: minor (deferred): strip()-based duplicate header check; whitespace-only trailing column trim; formula cells without cached value read as ""; corrupt/non-xlsx upload raises from read_workbook — Task 10 endpoint must catch and return 422/400
Task 4: fix round 1/5 (1 addressed pending re-review — wrong <dimension> dropped data; commits 8ac2390..9380d61)
Task 4: complete (commits 8af8d40..9380d61, review clean after 1 fix round)
Task 5: minor (deferred): int() on >4300-digit strings raises ValueError (guard len); numeric precision cap 1000; distinct/unique on unstripped values (importer must trim whitespace-only to NULL consistently with profiler); digit-only IDs without leading zero typed integer (UI override later)
Task 5: fix round 1/5 (1 addressed pending re-review — Unicode digits typed as integer; commits a2eeca2..d9bf90a)
Task 5: complete (commits 9380d61..d9bf90a, review clean after 1 fix round)
Ruling (Task 6): the plan's `accepted = (unmatched == 0)` is narrowed because the spec says a wrong FK silently breaks joins and a person must confirm suggestions: `accepted` is True only when unmatched == 0 AND the candidate is not ambiguous AND (source == "name" OR the column name, case-insensitively, contains the referenced table name). Containment-only candidates without that corroboration are still proposed but with accepted=False. At most one FK per (table, column): prefer source "name" over "containment", then highest matched fraction, then fewest unmatched, then profile order; if the top two candidates tie on all of those, emit the first with accepted=False — cost if wrong: the owner must tick a few more FK boxes in the UI.
Task 6: minor (deferred): PK with duplicates/nulls not validated before FKs proposed (downgrade at Apply/UI); O(cols*tables*rows) rescans; value comparison case-sensitive; stale "dictionary" comment; Sheet cells must be str (true for read_workbook)
Task 6: fix round 1/5 (2 addressed pending re-review; commits e3c3de5..93c8b86)
Task 6: minor (deferred): `named` substring corroboration is weak for very short table names; rowNo/no-PK variant untested; unused `order` variable
Task 6: complete (commits d9bf90a..93c8b86, review clean after 1 fix round)
Finding (Task 7): the real dictionary has a Master sheet defining the mt* master tables; only mtVoltageLevel and mtZone are undefined (2 missing FK targets). Update handoff/README: spec section 11 claim that mt* masters are undefined is now mostly wrong.
Task 7: minor (deferred): unparseable types become text with an Issue (UI must show dictionary issues prominently / treat as blocking); identifiers only stripped (DDL must quote-escape); non-matching "FK ..." notes dropped silently; stale comment; ref_column empty on unaccepted FKs; no test for FK to PK-less table
Task 7: complete (commits 93c8b86..0f6c34d, review clean)
Task 8: minor (deferred): silent inline UNIQUE on referenced non-PK unique column (comment it; ColumnProfile.unique is full-column exact-value from infer.py — resolved); accepted FKs with missing tables silently dropped (surface in Apply/UI); type allowlist admits varchar(0)/numeric(2000,5); no FK type-compat check; duplicate PK columns not rejected; DDL test gaps (no weird identifiers/composite PK/3-cycle on Postgres); table-level FOREIGN KEY form instead of inline REFERENCES (accepted)
Task 8: complete (commits 0f6c34d..d6e2193, review clean)
Ruling (Task 9): migration 0003_spp_api_create.sql (GRANT CREATE ON SCHEMA spp TO spp_api) was added in Task 9 because Apply needs it; Task 10 must NOT create another 0003 — it only adds the router. Tables created by Apply are owned by spp_api (spp_owner cannot read them; Superset gets SELECT via the explicit GRANT) — cost if wrong: owner-run SQL against imported tables needs an extra grant.
Task 9: minor (deferred): existing-table check uses information_schema (use pg_catalog); no advisory lock for concurrent imports; timestamptz naive (session TimeZone); proposal table without sheet created empty silently; test gaps (column order, short rows, skipped sheet not converted); missing annotation
Task 9: fix round 1/5 (2 addressed pending re-review — wide numerics, date-only timestamps; commits 03176be..05c06f4)
Task 9: complete (commits d6e2193..05c06f4, review clean after 1 fix round)
Task 10: minor (deferred): apply/status commit split; orphan upload file if insert fails; no app-level size/zip-bomb cap; unknown mode silently ignored; unauthenticated malformed JSON gets 422 before 401; empty-sheet proposal tables; thin PUT round-trip test; job stuck in `applying` if process dies mid-apply (only manual DB fix)
Task 10: fix round 1/5 (3 addressed pending re-review; commits d1927fa..d1db21b)
Task 10: fix round 2/5 (1 new finding pending re-review — unguarded final status write; commits d1db21b..144c8d4)
Task 10: minor (deferred): PUT has no row lock against apply claim
Task 10: minor (deferred): Path/proposal_json lines between claim and guarded block; PUT/upload with NUL in proposal/identifier strings -> jsonb DataError -> 500 (user-reachable; fix in a hardening pass); _fail_job catches Exception only
Task 10: complete (commits 05c06f4..144c8d4, review clean after 2 fix rounds)
Backend Tasks 2-10 done. Next: 12 (Superset), 14 (load into existing tables), 13 (e2e + README).
Ruling (Task 12): NO Celery broker/worker in v1 — the spec only asks for Redis as Superset cache and a worker adds a service nobody uses (YAGNI); the plan wording "Celery broker" is superseded — cost if wrong: async queries/alerts unavailable until a worker is added.
Task 12: minor (deferred): quote_plus -> quote(safe="") for URL userinfo in bootstrap.sh/superset_config.py (space in password becomes + and is not decoded); smoke only checks SPP exists not connectivity/user; smoke sources .env as shell; comment misplaced in superset_config; gunicorn sync workers (use gthread); proxy_buffer_size for big cookies; weak placeholder secret key not length-checked; unpinned pip installs; reader/admin passwords on argv inside container
Task 12: fix round 1/5 (3 addressed pending re-review; commits bcaedda..6c7d72a)
Task 12: complete (commits 144c8d4..6c7d72a, review clean after 1 fix round; superset image/commands unverifiable offline)
Ruling (Task 14): the missing scripts/smoke.sh change is not a defect — I told the implementer Task 13 owns the end-to-end smoke (including POST /loads). Identity/serial columns: REJECT with an Issue (sheet must omit auto-generated columns) instead of setval games — this product never creates such columns, only pre-existing foreign tables have them — cost if wrong: such tables cannot take explicit key values.
Task 14: minor (deferred): dead `except psycopg.Error` branch in load route; double-submit buttons (pattern-wide); load jobs share app.import_job with imports (no kind column; a failed load job is PUT-editable); 31-char Excel sheet-name limit vs 63-char table names; .xls accepted by file picker
Task 14: fix round 1/5 (2 addressed pending re-review; commits 7df6f0a..f5df232)
Task 14: complete (commits 6c7d72a..f5df232, review clean after 1 fix round)
Task 14: minor (deferred): non-owned nextval()/inherited serial defaults not caught by auto-generated check; header-less sheet with rows silently reports 0
Finding (Task 13 e2e on real dictionary): 58 tables / 17 FKs / 56 issues; apply rejected because dictionary.py emits bare varchar, numeric, jsonb for 34 columns and ddl.py/apply.py/load.py only support varchar(n), numeric(p,s) and no jsonb. No cycles, duplicate tables or duplicate columns. Missing FK targets: mtZone, mtVoltageLevel.
Ruling: fix at the source as new Task 15 (support unbounded varchar/numeric and jsonb in ddl.py, convert_value and the load type mapping); Task 13 work is parked in git stash "task13-wip" and resumes afterwards with the 34 script-side edits removed — cost if wrong: unbounded types accepted from dictionaries (valid Postgres) only.
Task 15: minor (deferred): hand-wrapped _SIMPLE literal; no backslash/newline COPY test for jsonb; varchar length cap; jsonb exponent cap 100000 is stricter than Postgres (131071) — rejects valid but absurd numbers; dictionary may emit `real`/`json` which ddl rejects (PUT returns 422 with a clear message)
Task 15: fix round 1/5 (1 addressed pending re-review; commits d51abcf..b503817)
Task 15: complete (commits ae538c5..b503817, review clean after 1 fix round)
Task 13: minor (deferred): KeyError on malformed 422 bodies in e2e; .env parser ignores `export` and inline comments; psycopg-binary pinned only transitively; e2e script could add -d spp itself; route coverage for step 8 is 5 routes (confirm no other authenticated routes)
Task 13: fix round 1/5 (1 addressed pending re-review — e2e re-runnable; commits b6e85c0..0ff12c3)
Task 13: complete (commits b503817..0ff12c3, review clean after 1 fix round)
Task 15/final: final whole-branch review done (no Critical; Important 1-5 fixed in commits 21468c8, 1ab50f8; scoped re-review clean). Remaining deferred minors are listed in docs/superpowers/HANDOFF.md.
