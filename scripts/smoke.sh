#!/usr/bin/env bash
# Usage: scripts/smoke.sh [section...]   (default: all sections)
# PSQL_CMD overrides how psql is reached, e.g.
#   PSQL_CMD="psql -h /tmp/pgtest -p 55432 -U postgres -tA" scripts/smoke.sh postgres
set -u
PSQL_CMD=${PSQL_CMD:-docker compose exec -T postgres psql -U postgres -tA}

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

wait_postgres() {
  local i tries=${SMOKE_RETRIES:-30}
  for ((i = 0; i < tries; i++)); do
    [ "$($PSQL_CMD -c 'SELECT 1' 2>/dev/null)" = "1" ] && return 0
    sleep 1
  done
  fail "postgres not reachable"
}

check_postgres() {
  local r d
  wait_postgres
  for r in spp_owner spp_api spp_reader superset; do
    [ "$($PSQL_CMD -c "SELECT 1 FROM pg_roles WHERE rolname='$r'")" = "1" ] || fail "role $r missing"
    ok "role $r"
  done
  for d in spp superset_meta; do
    [ "$($PSQL_CMD -c "SELECT 1 FROM pg_database WHERE datname='$d'")" = "1" ] || fail "database $d missing"
    ok "database $d"
  done
}

check_web() {
  local i tries=${SMOKE_RETRIES:-30} body code
  for ((i = 0; i < tries; i++)); do
    curl -fs http://localhost/ >/dev/null 2>&1 && break
    sleep 1
  done
  body=$(curl -fs http://localhost/) || fail "GET / failed"
  case "$body" in *SPP*) ok "GET / contains SPP" ;; *) fail "GET / missing SPP" ;; esac
  body=$(curl -fs http://localhost/api/healthz) || fail "GET /api/healthz failed"
  [ "$body" = '{"status":"ok"}' ] || fail "healthz body: $body"
  ok "GET /api/healthz"
  code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost/api/imports/00000000-0000-0000-0000-000000000000)
  [ "$code" = "401" ] || fail "unauthenticated /api/imports returned $code, want 401"
  ok "unauthenticated /api/imports is 401"
}

sections=("$@")
[ ${#sections[@]} -eq 0 ] && sections=(postgres)
for s in "${sections[@]}"; do
  case "$s" in
    postgres) check_postgres ;;
    web) check_web ;;
    *) fail "unknown section $s" ;;
  esac
done
