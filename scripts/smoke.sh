#!/usr/bin/env bash
# Usage: scripts/smoke.sh [section...]   (sections: postgres web e2e superset; default: all)
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

check_superset() {
  local i tries=${SMOKE_SUPERSET_RETRIES:-${SMOKE_RETRIES:-300}} body token dbs
  for ((i = 0; i < tries; i++)); do
    [ "$(curl -fs http://localhost/superset/health 2>/dev/null)" = "OK" ] && break
    (( i > 0 && i % 30 == 0 )) && echo "waiting for superset... (${i}s)"
    sleep 1
  done
  body=$(curl -fs http://localhost/superset/health) || fail "GET /superset/health failed"
  [ "$body" = "OK" ] || fail "superset health body: $body"
  ok "GET /superset/health"

  if [ -f .env ]; then
    set -a; . ./.env; set +a
  fi
  : "${SUPERSET_ADMIN_USER:?SUPERSET_ADMIN_USER not set}"
  : "${SUPERSET_ADMIN_PASSWORD:?SUPERSET_ADMIN_PASSWORD not set}"
  body=$(U="$SUPERSET_ADMIN_USER" P="$SUPERSET_ADMIN_PASSWORD" python3 -c \
    'import json,os; print(json.dumps({"username":os.environ["U"],"password":os.environ["P"],"provider":"db","refresh":True}))')
  token=$(curl -fs -X POST -H 'Content-Type: application/json' --data @- \
    http://localhost/superset/api/v1/security/login <<<"$body" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])') \
    || fail "superset login failed"
  ok "superset admin login"
  dbs=$(printf 'header = "Authorization: Bearer %s"\n' "$token" \
    | curl -fs --config - http://localhost/superset/api/v1/database/ \
    | python3 -c 'import json,sys; print("\n".join(r["database_name"] for r in json.load(sys.stdin)["result"]))') \
    || fail "superset database list failed"
  printf '%s\n' "$dbs" | grep -qx 'SPP' || fail "database SPP not found in superset (got: $dbs)"
  ok "superset database SPP exists"
}

check_e2e() {
  command -v python3 >/dev/null || fail "python3 not found"
  PSQL_CMD="$PSQL_CMD -d spp" python3 scripts/e2e.py || fail "e2e failed"
}

sections=("$@")
[ ${#sections[@]} -eq 0 ] && sections=(postgres web e2e superset)
for s in "${sections[@]}"; do
  case "$s" in
    postgres) check_postgres ;;
    web) check_web ;;
    e2e) check_e2e ;;
    superset) check_superset ;;
    *) fail "unknown section $s" ;;
  esac
done
