# Handoff: SPP stack and Excel import

Date: 2026-11-02. Branch: `feat/frontend-nginx-superset`.

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

- Spec section 5: use-case ERD / view builder.
- Spec section 7: `/schema`, `/usecases`, `/projects`, `/partners`.
- Celery (decided against; imports run in the request).
- User accounts (single admin password only).

## How the work was run

Subagent-driven development: one implementer per task, then spec and quality reviews, then a scoped re-review after fixes, then a final whole-branch review. The progress ledger is `sdd-ledger.md` next to this file.
