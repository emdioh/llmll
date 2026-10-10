"""FastAPI application factory."""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI

from app.api import (
    auth,
    backup,
    contests,
    debug,
    explain,
    grammar,
    health,
    items,
    learner,
    placement,
    progress,
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
from app.llm.factory import LLMConfigError, build_llm_client
from app.llm.live import LiveBroadcaster
from app.llm.overrides import OverrideIn, apply_overrides, load_overrides
from app.nlp.analyzer import Analyzer, SpacyAnalyzer
from app.nlp.extract import ExtractedText, fetch_article
from app.nlp.languagetool import LanguageToolClient
from app.services.contests import ContestResolver, build_resolver
from app.store.db import create_session_factory
from app.store.schema_version import schema_status

logger = logging.getLogger(__name__)


def _warn_if_schema_outdated(session_factory: Any) -> bool:
    """A server started on a database missing migrations fails on the first query with a
    confusing SQL error: say what to do as soon as it starts. Returns whether it is current."""
    try:
        status = schema_status(session_factory.kw["bind"])
    except Exception:  # never prevent startup because of the check itself
        return False
    if not status.up_to_date:
        logger.error("%s", status.message())
    return status.up_to_date


def _stored_overrides(session_factory: Any) -> dict[str, OverrideIn]:
    try:
        with session_factory() as session:
            return load_overrides(session)
    except Exception:  # an unreadable table must not prevent startup
        logger.exception("could not read the stored LLM overrides; ignoring them")
        return {}


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
    schema_ok = _warn_if_schema_outdated(app.state.session_factory)
    # Holds events only when LLMLL_DEBUG=true (they contain full prompts and responses).
    app.state.live = LiveBroadcaster(enabled=settings.debug)
    app.state.llm_override_error = None
    app.state.llm_lock = threading.Lock()
    if llm is not None:
        app.state.llm_builder = None
        app.state.llm = llm
    else:
        session_factory, live, clock = app.state.session_factory, app.state.live, app.state.now

        def builder(overrides: dict[str, OverrideIn]) -> LLMClient:
            return build_llm_client(
                apply_overrides(settings, overrides), session_factory, clock, live
            )

        app.state.llm_builder = builder
        stored = _stored_overrides(session_factory) if schema_ok else {}
        try:
            app.state.llm = builder(stored)
        except LLMConfigError as exc:
            if not stored:
                raise
            # e.g. the key of an overridden provider was removed from .env
            logger.error("the stored LLM model overrides cannot be applied: %s", exc)
            app.state.llm_override_error = str(exc)
            app.state.llm = builder({})
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
        progress.router,
        stats.router,
        debug.router,
        backup.router,
    ):
        app.include_router(router, dependencies=[Depends(require_auth)])
    if settings.frontend_dist is not None and settings.frontend_dist.is_dir():
        # Mounted last so that /api/* routes take precedence.
        app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
    return app
