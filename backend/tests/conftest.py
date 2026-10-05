from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent


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
def client(migrated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(migrated_settings)) as test_client:
        yield test_client
