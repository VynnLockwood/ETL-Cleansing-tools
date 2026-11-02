ARG SUPERSET_VERSION=5.0.0
FROM apache/superset:${SUPERSET_VERSION}

USER root
# The official 5.x image keeps its virtualenv at /app/.venv and may not ship pip.
RUN if command -v uv >/dev/null 2>&1; then \
      uv pip install --python /app/.venv/bin/python psycopg2-binary redis; \
    else \
      /app/.venv/bin/python -m pip install --no-cache-dir psycopg2-binary redis; \
    fi

COPY superset_config.py /app/pythonpath/superset_config.py
COPY --chmod=755 bootstrap.sh /app/docker/bootstrap.sh

USER superset
