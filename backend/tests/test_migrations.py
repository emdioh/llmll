from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

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
