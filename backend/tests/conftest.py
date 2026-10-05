from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.curriculum.importer import import_curriculum
from app.curriculum.loader import load_curriculum
from app.main import create_app
from app.store.db import create_session_factory

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Clock:
    """Controllable time provider."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def import_fixture(settings: Settings, name: str = "curriculum", when: datetime | None = None):
    curriculum = load_curriculum(FIXTURES / name)
    with create_session_factory(settings)() as session:
        report = import_curriculum(session, curriculum, when or datetime.now(UTC))
        session.commit()
    return report


def make_alembic_config(database_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def migrated_settings(settings: Settings) -> Settings:
    command.upgrade(make_alembic_config(settings.database_url), "head")
    return settings


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 10, 5, 9, 0, tzinfo=UTC))


@pytest.fixture
def client(migrated_settings: Settings, clock: Clock) -> Iterator[TestClient]:
    with TestClient(create_app(migrated_settings, now=clock)) as test_client:
        yield test_client


@pytest.fixture
def curriculum_client(client: TestClient, migrated_settings: Settings, clock: Clock) -> TestClient:
    """A client whose database already holds the fixture curriculum."""
    import_fixture(migrated_settings, when=clock.now)
    return client
