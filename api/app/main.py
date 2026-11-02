from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db
from .auth import admin_auth
from .config import Config, load_config
from .imports.router import build_router
from .usecases.router import build_router as build_usecases_router


def create_app(cfg: Config | None = None) -> FastAPI:
    if cfg is None:
        cfg = load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.migrate(cfg.migrate_database_url)
        pool = db.open_pool(cfg.database_url)
        reader_pool = db.open_pool(cfg.reader_database_url) if cfg.reader_database_url else None
        app.state.pool = pool
        app.state.reader_pool = reader_pool
        app.state.cfg = cfg
        try:
            yield
        finally:
            if reader_pool is not None:
                reader_pool.close()
            pool.close()

    app = FastAPI(
        title="SPP API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.cfg = cfg

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    admin = admin_auth(cfg.admin_password)
    app.include_router(build_router(admin))
    app.include_router(build_usecases_router(admin))

    return app
