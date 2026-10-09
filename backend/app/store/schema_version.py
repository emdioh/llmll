"""Is the database at the schema version this code expects? (Alembic head)"""

from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

BACKEND_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SchemaStatus:
    current: tuple[str, ...]
    expected: tuple[str, ...]

    @property
    def up_to_date(self) -> bool:
        return set(self.current) == set(self.expected)

    def message(self) -> str:
        current = ", ".join(self.current) or "empty (never migrated)"
        return (
            f"the database schema is out of date (at {current}, this code needs "
            f"{', '.join(self.expected)}). Run: cd backend && uv run alembic upgrade head "
            "(the scripts in scripts/ and the Docker image do this automatically)"
        )


def schema_status(engine: Engine) -> SchemaStatus:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    expected = tuple(ScriptDirectory.from_config(config).get_heads())
    with engine.connect() as connection:
        current = tuple(MigrationContext.configure(connection).get_current_heads())
    return SchemaStatus(current=current, expected=expected)
