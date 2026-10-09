"""Clear errors instead of SQL tracebacks when migrations are pending."""

from pathlib import Path

import pytest
from alembic import command

from app.cli import main as cli_main
from app.config import Settings, get_settings
from app.main import create_app
from app.store.db import create_db_engine
from app.store.schema_version import schema_status

from .conftest import make_alembic_config


def migrated_to(tmp_path: Path, revision: str) -> str:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    command.upgrade(make_alembic_config(url), revision)
    return url


def test_schema_status(tmp_path: Path) -> None:
    url = migrated_to(tmp_path, "0007")
    status = schema_status(create_db_engine(url))
    assert not status.up_to_date and status.current == ("0007",)
    assert "alembic upgrade head" in status.message()
    command.upgrade(make_alembic_config(url), "head")
    assert schema_status(create_db_engine(url)).up_to_date


def test_cli_db_command_explains_pending_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLMLL_DATABASE_URL", migrated_to(tmp_path, "0007"))
    get_settings.cache_clear()
    try:
        assert cli_main(["llm-stats"]) == 1
        err = capsys.readouterr().err
        assert "schema is out of date" in err and "alembic upgrade head" in err
        assert "Traceback" not in err
    finally:
        get_settings.cache_clear()


def _capture_errors(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    # Alembic's fileConfig (run by the migrations above) replaces the root log handlers, which
    # removes pytest's capture handler: record the logger's calls directly instead.
    import app.main

    messages: list[str] = []
    monkeypatch.setattr(app.main.logger, "error", lambda fmt, *a: messages.append(fmt % a))
    return messages


def test_app_logs_an_error_at_startup_on_an_outdated_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(database_url=migrated_to(tmp_path, "0007"))
    errors = _capture_errors(monkeypatch)
    create_app(settings)
    assert any("schema is out of date" in e for e in errors)


def test_no_warning_when_current(
    migrated_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = _capture_errors(monkeypatch)
    create_app(migrated_settings)
    assert errors == []
