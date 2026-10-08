from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.config import Settings
from app.store.models import Base

from .conftest import make_alembic_config


def test_upgrade_and_downgrade(settings: Settings) -> None:
    cfg = make_alembic_config(settings.database_url)
    engine = create_engine(settings.database_url)

    command.upgrade(cfg, "head")
    assert "learners" in inspect(engine).get_table_names()

    command.downgrade(cfg, "base")
    assert "learners" not in inspect(engine).get_table_names()


def test_models_match_migrations(settings: Settings) -> None:
    command.upgrade(make_alembic_config(settings.database_url), "head")
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_provider_backfill(settings: Settings) -> None:
    cfg = make_alembic_config(settings.database_url)
    engine = create_engine(settings.database_url)
    command.upgrade(cfg, "0006")
    columns = "ts, task, prompt_version, model, request, latency_ms"
    with engine.begin() as connection:
        for model in ("claude-opus-5-5", "fake"):
            connection.execute(
                text(
                    f"INSERT INTO llm_calls ({columns}) "
                    f"VALUES ('2026-10-05 00:00:00', 'gloss', 'v1', '{model}', '{{}}', 1)"
                )
            )
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT model, provider FROM llm_calls ORDER BY id"))
        assert rows.all() == [("claude-opus-5-5", "anthropic"), ("fake", "fake")]


def test_timing_columns_migration(settings: Settings) -> None:
    cfg = make_alembic_config(settings.database_url)
    engine = create_engine(settings.database_url)
    command.upgrade(cfg, "0007")
    before = {c["name"] for c in inspect(engine).get_columns("llm_calls")}
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO llm_calls (ts, task, prompt_version, model, request, latency_ms) "
                "VALUES ('2026-10-05 00:00:00', 'gloss', 'v1', 'm', '{}', 1)"
            )
        )
    command.upgrade(cfg, "head")
    new = {
        "attempts",
        "http_statuses",
        "retry_wait_ms",
        "ttfb_ms",
        "download_ms",
        "overhead_ms",
        "reasoning_tokens",
        "upstream_provider",
        "request_chars",
    }
    columns = {c["name"]: c for c in inspect(engine).get_columns("llm_calls")}
    assert not new & before and new <= set(columns)
    assert all(columns[name]["nullable"] for name in new)
    with engine.connect() as connection:  # existing rows keep working, with nulls
        row = connection.execute(text("SELECT attempts, http_statuses, ttfb_ms FROM llm_calls"))
        assert row.one() == (None, None, None)
    command.downgrade(cfg, "0007")
    assert not new & {c["name"] for c in inspect(engine).get_columns("llm_calls")}
