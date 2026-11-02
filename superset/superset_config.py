import os
from urllib.parse import quote_plus

# SUPERSET_APP_ROOT (/superset) is read from the environment by Superset itself.

SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SUPERSET_SECRET_KEY must be set")

SQLALCHEMY_DATABASE_URI = (
    "postgresql+psycopg2://superset:"
    f"{quote_plus(os.environ['SUPERSET_DB_PASSWORD'])}@postgres:5432/superset_meta"
)

# Behind nginx: trust X-Forwarded-* headers.
ENABLE_PROXY_FIX = True

# Near-live data: 1 second default. Individual datasets/charts can override
# their own cache timeout. A timeout of 0 means "no caching" only in some
# Superset versions, so 1 is used for safety.
CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_HOST": "redis",
    "CACHE_REDIS_PORT": 6379,
    "CACHE_REDIS_DB": 0,
}
DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 1,
    "CACHE_KEY_PREFIX": "superset_data_",
    "CACHE_REDIS_HOST": "redis",
    "CACHE_REDIS_PORT": 6379,
    "CACHE_REDIS_DB": 0,
}
