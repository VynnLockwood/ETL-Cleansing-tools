from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db
from .auth import admin_auth
from .config import Config, load_config
from .imports.router import build_router


def create_app(cfg: Config | None = None) -> FastAPI:
    if cfg is None:
        cfg = load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.migrate(cfg.migrate_database_url)
        pool = db.open_pool(cfg.database_url)
        app.state.pool = pool
        app.state.cfg = cfg
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(
        title="SPP API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.cfg = cfg

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(build_router(admin_auth(cfg.admin_password)))

    return app
