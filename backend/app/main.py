"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import health
from app.config import Settings, get_settings
from app.store.db import create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="LLMLL")
    app.state.settings = settings
    app.state.session_factory = create_session_factory(settings)
    app.include_router(health.router)
    if settings.frontend_dist is not None and settings.frontend_dist.is_dir():
        # Mounted last so that /api/* routes take precedence.
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
    return app
