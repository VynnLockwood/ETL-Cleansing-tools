# SPP database service

A Docker stack that turns Excel files into PostgreSQL tables for the SPP project data and lets you explore them in Superset.

- **nginx** serves the import page at `/` and proxies `/api/` and `/superset/`.
- **api** (Python, FastAPI) reads an uploaded workbook, proposes tables and keys, creates them in schema `spp`, and loads data.
- **postgres** (16) holds the data (`spp`), the import bookkeeping (`app`) and Superset's metadata (`superset_meta`).
- **superset** reads the data through the read-only role `spp_reader`.
- **redis** is used by Superset.

## Prerequisites

Docker with the Compose plugin. Nothing else is needed to run the stack.

## Start

```sh
cp .env.example .env      # then edit .env and replace every change-me value
docker compose up -d --build --wait
```

| URL | What |
| --- | --- |
| `http://localhost/` | Import page (asks for the admin password) |
| `http://localhost/api/healthz` | API health check |
| `http://localhost/superset/` | Superset (`SUPERSET_ADMIN_USER` / `SUPERSET_ADMIN_PASSWORD` from `.env`) |

The API uses HTTP Basic auth as `admin` with `ADMIN_PASSWORD`; only `/healthz` is open (the interactive API docs pages `/docs`, `/redoc` and `/openapi.json` are disabled).

## Import flow

1. **Dictionary first.** Tick "dictionary mode" and upload `docs/data-dict-spp.xlsx`. The API proposes one table per dictionary sheet, with primary keys and foreign keys, and lists parse issues. Review the proposal, untick foreign keys you do not want, optionally add the standard audit columns, save, then Apply. Tables that already exist are skipped, not changed.
2. **Then data.** Use "Load data into existing tables": upload a workbook whose sheets are named after existing tables and whose headers are column names. Sheets are loaded parents-first (foreign-key order, not sheet order). The load is all-or-nothing: if any row is rejected nothing is loaded and the issues are listed.

Without dictionary mode, an ordinary workbook is profiled instead: column types are inferred and keys are detected from the data.

## Tests

```sh
cd api
pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql://spp_owner:PASSWORD@HOST:5432/spp python -m pytest -q -W error
```

The database tests drop and recreate the `spp`, `app` and `usecase` schemas, so point them at a throwaway database. They skip when `TEST_DATABASE_URL` is unset. The URL must connect as the owner role (`spp_owner`). Some tests also connect as the `spp_api` and `spp_reader` roles with the same host, port and database; their passwords default to `apipw` and `readerpw` and can be set with `TEST_SPP_API_PASSWORD` and `TEST_SPP_READER_PASSWORD`.

### Smoke and end-to-end checks

```sh
scripts/smoke.sh                 # postgres, web, e2e, superset
scripts/smoke.sh postgres web    # chosen sections only
```

| Section | Checks |
| --- | --- |
| `postgres` | roles `spp_owner`, `spp_api`, `spp_reader`, `superset` and databases `spp`, `superset_meta` exist |
| `web` | `/` serves the page, `/api/healthz` answers, `/api/imports/...` is 401 without credentials |
| `e2e` | runs `scripts/e2e.py` (dictionary upload, apply, re-apply, data load, repeat load, auth) |
| `superset` | `/superset/health`, admin login, database `SPP` exists |

`scripts/e2e.py` uses only the Python standard library and can target any base URL: `E2E_API_BASE` (default `http://localhost/api`), `ADMIN_PASSWORD` (environment or `.env`), and optionally `PSQL_CMD` for the database assertions. `scripts/smoke.sh e2e` passes its own `PSQL_CMD` plus `-d spp` to the script; run on its own, the script needs `PSQL_CMD` to include `-d spp`, for example `PSQL_CMD="docker compose exec -T postgres psql -U postgres -tA -d spp"`.
The e2e check can be re-run on the same stack: it applies the dictionary (tables that already exist are skipped) and loads rows with random UUID keys, so runs do not collide. Each run leaves 4 data rows behind (2 in `sppBusinessPartner`, 2 in `sppProject`). It also creates the dictionary tables if they are missing, so do not point it at a stack whose `spp` schema holds other data you care about.

## Notes

- The API gets its database passwords as separate variables (`SPP_API_PASSWORD`, `SPP_OWNER_PASSWORD`; optional `SPP_DB_HOST`, `SPP_DB_PORT`, `SPP_DB_NAME`, `SPP_API_USER`, `SPP_OWNER_USER`), so they may contain any characters such as `/` or `%` and need no URL encoding. `DATABASE_URL` / `MIGRATE_DATABASE_URL` still work as explicit overrides.
- The role passwords in `.env` only take effect on a fresh Postgres volume. Changing them later does not change the existing roles; use `ALTER ROLE` or recreate the volume (`docker compose down -v` deletes all data).
- Superset reads with `spp_reader` (SELECT only, including on tables created later).
- Imported tables are owned by `spp_api`.
- The API image runs as the non-root user `app` (uid 10001). Uploaded files go to `/data/uploads`, owned by `app` in the image. A Docker named volume mounted there copies that ownership only when the volume is first created and empty; a volume created earlier by a root-running image keeps `root` ownership and the API then cannot save uploads. Fix with `docker compose run --rm --user root api chown -R 10001:10001 /data/uploads`, or recreate the volume.

## Known limitations

- If the API crashes (or is killed) during an apply, the job stays in status `applying` and cannot be applied again. Reset it by hand:
  `UPDATE app.import_job SET status = 'failed' WHERE id = '<job id>';`
- There are no user accounts, only the single admin password.
- No background jobs: imports run inside the request (no Celery).

## Known issues in the data dictionary

`docs/data-dict-spp.xlsx` has 47 sheets (46 tables plus the `Master` sheet, which defines 12 `mt*` master tables), giving 58 proposed tables. Dictionary mode reports these as issues instead of failing:

- Blank headers, and duplicate columns in `sppCaseExtendScod` (the duplicates are ignored).
- Misaligned columns: in `sppCaseConnectGenerator`, `sppCaseConnectGenConnection` and `sppCaseConnectGenInspectGen` the type cell holds a Thai description, or is empty, so the type falls back to `text`. Several `isDelete`/`*Date` columns have a type that does not match their name (for example `documentDate` is boolean in `sppDocProcExtendScod`).
- Foreign keys to `mtVoltageLevel` and `mtZone` point at tables the dictionary does not define; they are left unaccepted.
- Unit mismatches in the descriptions (`genContractCapacityW` described as MW, `distanceToConnectionM` as km, `bessCapacityWh` as MW-hr), `peaConnectionPointA`/`B` both mapped to source column `AU`, and a PEA address placed in the plant address column of `sppProjectAddress`.
