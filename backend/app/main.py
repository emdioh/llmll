"""FastAPI application factory."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends, FastAPI

from app.api import (
    auth,
    contests,
    debug,
    explain,
    grammar,
    health,
    items,
    learner,
    placement,
    queue,
    reading,
    sessions,
    stats,
)
from app.api import settings as settings_api
from app.api.auth import LoginThrottle, require_auth
from app.api.frontend import SPAStaticFiles
from app.config import Settings, get_settings
from app.llm.client import LLMClient
from app.llm.factory import build_llm_client
from app.llm.live import LiveBroadcaster
from app.nlp.analyzer import Analyzer, SpacyAnalyzer
from app.nlp.extract import ExtractedText, fetch_article
from app.nlp.languagetool import LanguageToolClient
from app.services.contests import ContestResolver, build_resolver
from app.store.db import create_session_factory


def create_app(
    settings: Settings | None = None,
    now: Callable[[], datetime] | None = None,
    llm: LLMClient | None = None,
    languagetool: LanguageToolClient | None = None,
    analyzer: Analyzer | None = None,
    article_fetcher: Callable[[str], ExtractedText] | None = None,
    contest_resolver: ContestResolver | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="LLMLL")
    app.state.settings = settings
    app.state.now = now or (lambda: datetime.now(UTC))
    app.state.session_factory = create_session_factory(settings)
    # Holds events only when LLMLL_DEBUG=true (they contain full prompts and responses).
    app.state.live = LiveBroadcaster(enabled=settings.debug)
    app.state.llm = llm or build_llm_client(
        settings, app.state.session_factory, app.state.now, app.state.live
    )
    app.state.languagetool = languagetool or LanguageToolClient(settings.languagetool_url)
    # The spaCy model is loaded lazily on the first analysis.
    app.state.analyzer = analyzer or SpacyAnalyzer()
    app.state.article_fetcher = article_fetcher or fetch_article
    app.state.contest_resolver = contest_resolver or build_resolver(settings.contest_resolver)
    app.state.login_throttle = LoginThrottle()
    app.include_router(health.router)
    app.include_router(auth.router)
    for router in (
        learner.router,
        settings_api.router,
        sessions.router,
        items.router,
        grammar.router,
        explain.router,
        reading.router,
        queue.router,
        contests.router,
        placement.router,
        stats.router,
        debug.router,
    ):
        app.include_router(router, dependencies=[Depends(require_auth)])
    if settings.frontend_dist is not None and settings.frontend_dist.is_dir():
        # Mounted last so that /api/* routes take precedence.
        app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
    return app
