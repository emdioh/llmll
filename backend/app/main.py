"""FastAPI application factory."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import FastAPI

from app.api import explain, grammar, health, items, learner, sessions
from app.api import settings as settings_api
from app.api.frontend import SPAStaticFiles
from app.config import Settings, get_settings
from app.llm.client import LLMClient
from app.llm.factory import build_llm_client
from app.nlp.languagetool import LanguageToolClient
from app.store.db import create_session_factory


def create_app(
    settings: Settings | None = None,
    now: Callable[[], datetime] | None = None,
    llm: LLMClient | None = None,
    languagetool: LanguageToolClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="LLMLL")
    app.state.settings = settings
    app.state.now = now or (lambda: datetime.now(UTC))
    app.state.session_factory = create_session_factory(settings)
    app.state.llm = llm or build_llm_client(settings, app.state.session_factory, app.state.now)
    app.state.languagetool = languagetool or LanguageToolClient(settings.languagetool_url)
    for router in (
        health.router,
        learner.router,
        settings_api.router,
        sessions.router,
        items.router,
        grammar.router,
        explain.router,
    ):
        app.include_router(router)
    if settings.frontend_dist is not None and settings.frontend_dist.is_dir():
        # Mounted last so that /api/* routes take precedence.
        app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
    return app
