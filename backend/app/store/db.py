"""Engine and session handling, derived from the application settings."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def create_db_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args: dict[str, object] = {}
    is_sqlite = url.get_backend_name() == "sqlite"
    if is_sqlite:
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args=connect_args)
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(settings.database_url), expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: a session bound to the engine stored on `app.state`."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session
