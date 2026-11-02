#!/usr/bin/env bash
# Creates SPP roles and databases. Works as a docker-entrypoint-initdb.d script
# and directly against a server via PGHOST/PGPORT/PGUSER.
set -euo pipefail

: "${SPP_OWNER_PASSWORD:?}" "${SPP_API_PASSWORD:?}" "${SPP_READER_PASSWORD:?}" "${SUPERSET_DB_PASSWORD:?}"

run() { psql -v ON_ERROR_STOP=1 -d "${PGDATABASE:-postgres}" "$@"; }

run \
  -v owner_pw="$SPP_OWNER_PASSWORD" -v api_pw="$SPP_API_PASSWORD" \
  -v reader_pw="$SPP_READER_PASSWORD" -v superset_pw="$SUPERSET_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', r.name, r.pw)
FROM (VALUES ('spp_owner', :'owner_pw'), ('spp_api', :'api_pw'),
             ('spp_reader', :'reader_pw'), ('superset', :'superset_pw')) AS r(name, pw)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.name)
\gexec

SELECT 'CREATE DATABASE spp OWNER spp_owner'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'spp')
\gexec

SELECT 'CREATE DATABASE superset_meta OWNER superset'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'superset_meta')
\gexec

REVOKE CONNECT ON DATABASE spp FROM PUBLIC;
GRANT CONNECT ON DATABASE spp TO spp_owner, spp_api, spp_reader;
REVOKE CONNECT ON DATABASE superset_meta FROM PUBLIC;
GRANT CONNECT ON DATABASE superset_meta TO superset;
SQL
