from __future__ import annotations

from fastapi import APIRouter, FastAPI

from .db import Base, make_sessionmaker
from .errors import install_error_handlers
from .routes import assets, exports, jobs, projects, sessions, shares, storage
from .storage import make_storage
from .settings import Settings


def create_app(settings: Settings | None = None, *, create_tables: bool | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Open Creative Apps API", version="0.1.0", openapi_url="/api/v1/openapi.json",
                  docs_url="/api/v1/docs")
    app.state.settings = settings
    app.state.db = make_sessionmaker(settings.database_url)
    if create_tables if create_tables is not None else settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(app.state.db.kw["bind"])
    app.state.storage = make_storage(settings)
    install_error_handlers(app)
    api = APIRouter(prefix="/api/v1")
    for module in (sessions, projects, assets, storage, jobs, exports, shares):
        api.include_router(module.router)
    app.include_router(api)
    return app
