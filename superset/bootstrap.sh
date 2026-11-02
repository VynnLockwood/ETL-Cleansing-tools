#!/usr/bin/env bash
set -euo pipefail

superset db upgrade

# Create the admin, or rotate its password on rerun. Credentials come from ENV
# (never argv); any real error aborts the script via set -e.
python - <<'PY'
import os

from superset.app import create_app

USER = os.environ["SUPERSET_ADMIN_USER"]
EMAIL = os.environ["SUPERSET_ADMIN_EMAIL"]
PASSWORD = os.environ["SUPERSET_ADMIN_PASSWORD"]

app = create_app()
with app.app_context():
    sm = app.appbuilder.sm
    user = sm.find_user(username=USER)
    if user is None:
        created = sm.add_user(
            username=USER,
            first_name="Admin",
            last_name="User",
            email=EMAIL,
            role=sm.find_role("Admin"),
            password=PASSWORD,
        )
        if not created:
            raise SystemExit("failed to create admin user")
        print("admin user created")
    else:
        sm.reset_password(user.id, PASSWORD)
        print("admin password updated")
PY

superset init

READER_PW=$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ["SPP_READER_PASSWORD"], safe=""))')
superset set_database_uri -d SPP \
  -u "postgresql+psycopg2://spp_reader:${READER_PW}@postgres:5432/spp"

exec gunicorn --bind 0.0.0.0:8088 --workers 2 --timeout 120 "superset.app:create_app()"
