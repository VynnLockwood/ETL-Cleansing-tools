#!/usr/bin/env bash
# Usage: scripts/smoke.sh [section...]   (default: all sections)
# PSQL_CMD overrides how psql is reached, e.g.
#   PSQL_CMD="psql -h /tmp/pgtest -p 55432 -U postgres -tA" scripts/smoke.sh postgres
set -u
PSQL_CMD=${PSQL_CMD:-docker compose exec -T postgres psql -U postgres -tA}

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

check_postgres() {
  local r d
  for r in spp_owner spp_api spp_reader superset; do
    [ "$($PSQL_CMD -c "SELECT 1 FROM pg_roles WHERE rolname='$r'")" = "1" ] || fail "role $r missing"
    ok "role $r"
  done
  for d in spp superset_meta; do
    [ "$($PSQL_CMD -c "SELECT 1 FROM pg_database WHERE datname='$d'")" = "1" ] || fail "database $d missing"
    ok "database $d"
  done
}

sections=("$@")
[ ${#sections[@]} -eq 0 ] && sections=(postgres)
for s in "${sections[@]}"; do
  case "$s" in
    postgres) check_postgres ;;
    *) fail "unknown section $s" ;;
  esac
done
