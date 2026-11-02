# Handoff: SPP stack, Excel import and use case checker

Date: 2026-11-02. The stack and import were built on `feat/frontend-nginx-superset` (merged into `master`); the use case checker is on `feat/use-case-checker`.

## Use case checker (spec `2026-11-02-use-case-checker-design.md`, plan `2026-11-02-use-case-checker.md`)

- Built: migration `0005`, reader pool, catalog graph, use case CRUD, resolver, query planner, read-only preview, six recommendation rules, test endpoint with ERD data, the page `frontend/usecases.html` (+ `erd.js`), an e2e step, README section.
- Verified: 354 pytest tests (`-W error`) and 7 `node --test` tests; `scripts/e2e.py` run twice in a row against the real dictionary schema on a local Postgres 16; the page was driven in real Chromium (Playwright) against the real API: connect, build a 4-field use case, test, ERD, query tabs, delete, wrong password.
- Never run: `docker compose up` with this branch (compose only gained `SPP_READER_PASSWORD` for the `api` service; checked with `docker compose config`), nginx routing of `/usecases.html` (the frontend image now copies `usecases.html` and `erd.js`).
- Finding on your data: columns `sppBusinessPartner.mtTypeEntity`, `mtAccountClass`, `mtTitle`, `mtGender`, `mtStatusMarital`, `mtTypeLegalEntity` have no foreign key and no table of that name (reported as `MISSING_TABLE`), besides `mtZone` and `mtVoltageLevel`. `sppProjectAddress.sppProject` has no foreign key to `sppProject`, so the address table is unreachable from a project (suggested key shown).
- Decisions beyond the spec (rulings) and the deferred minor items are in `docs/superpowers/sdd-ledger-use-case-checker.md`.
- Not built (spec section 2, later): path pinning, "one record" key filter, what-if test of a suggested key, schema-change regression, publish as a Superset dataset, draft mode without tables.
- The earlier stack/import ledger is `docs/superpowers/sdd-ledger.md`.

## Earlier work: stack and Excel import

## Where things stand

- Backend is **Python / FastAPI** (psycopg 3, openpyxl, pytest). Tasks 1 to 15 of the plan are implemented on this branch.
- The branch is based on `master` at `c4d949d`, which holds another agent's Go API commit. This branch deliberately deletes that Go code, so merging requires resolving `api/` (keep the Python version).
- Spec: `docs/superpowers/specs/2026-11-02-spp-service-design.md` (approved, implemented). Plan: `docs/superpowers/plans/2026-11-02-spp-stack-and-excel-import.md`.
- The rulings made during execution and the per-task review history are in `docs/superpowers/sdd-ledger.md`.

## What was verified

- 238 pytest tests (244 after the final review fixes) pass with `-W error` against a local PostgreSQL 16.
- `scripts/e2e.py` was run twice against the real `docs/data-dict-spp.xlsx` with a local uvicorn and the local Postgres 16: 58 tables created, 15 accepted foreign keys created, data load works, and a primary-key violation rolls the whole load back.

## Never run

- `docker compose up`, the image builds, nginx routing.
- The Superset image and `superset/bootstrap.sh`.
- Every `scripts/smoke.sh` section, except `e2e.py` against a local uvicorn (not through nginx).

## Known limitations and deferred hardening

- A job stuck in `applying` after a crash needs a manual `UPDATE` (see README).
- A NUL character in a PUT proposal gives a 500.
- Formula cells without a cached value read as empty.
- Integer cells with more than 4300 digits are not handled.
- The existing-table check uses `information_schema` instead of `pg_catalog`.
- No advisory lock for concurrent imports.
- The frontend buttons can be double-submitted.
- No application-level upload size cap.
- The Superset smoke check only verifies that the SPP database exists.
- Uploaded files are never deleted.

## Not built

- `/projects` and `/partners` CRUD endpoints (the use case checker covers `/schema/tables` and `/usecases`).
- Celery (decided against; imports run in the request).
- User accounts (single admin password only).

## How the work was run

Subagent-driven development: one implementer per task, then spec and quality reviews, then a scoped re-review after fixes, then a final whole-branch review. The progress ledger is `sdd-ledger.md` next to this file.
