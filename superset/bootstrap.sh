#!/usr/bin/env bash
set -euo pipefail

superset db upgrade

# Ignore failure so reruns work when the admin user already exists.
superset fab create-admin \
  --username "${SUPERSET_ADMIN_USER}" \
  --firstname Admin \
  --lastname User \
  --email "${SUPERSET_ADMIN_EMAIL}" \
  --password "${SUPERSET_ADMIN_PASSWORD}" || echo "create-admin: user already exists (or failed); continuing"

superset init

READER_PW=$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote_plus(os.environ["SPP_READER_PASSWORD"]))')
superset set_database_uri -d SPP \
  -u "postgresql+psycopg2://spp_reader:${READER_PW}@postgres:5432/spp"

exec gunicorn --bind 0.0.0.0:8088 --workers 2 --timeout 120 "superset.app:create_app()"
