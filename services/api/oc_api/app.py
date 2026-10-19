from __future__ import annotations

from fastapi import APIRouter, FastAPI

from .db import Base, make_sessionmaker
from .errors import install_error_handlers
from .routes import projects, sessions
from .settings import Settings


def create_app(settings: Settings | None = None, *, create_tables: bool | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Open Creative Apps API", version="0.1.0", openapi_url="/api/v1/openapi.json",
                  docs_url="/api/v1/docs")
    app.state.settings = settings
    app.state.db = make_sessionmaker(settings.database_url)
    if create_tables if create_tables is not None else settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(app.state.db.kw["bind"])
    install_error_handlers(app)
    api = APIRouter(prefix="/api/v1")
    for module in (sessions, projects):
        api.include_router(module.router)
    app.include_router(api)
    return app
