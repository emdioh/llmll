import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from .conftest import Clock
from .test_auth import TOKEN, make_client


def test_backup_writes_a_consistent_copy_next_to_the_database(
    client: TestClient, migrated_settings: Settings
) -> None:
    assert client.post("/api/learner", json={"level": "A1"}).status_code == 201
    resp = client.post("/api/backup")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    db_file = Path(migrated_settings.database_url.removeprefix("sqlite:///"))
    target = db_file.with_name("llmll-backup.db")
    assert Path(body["path"]) == target and body["size_bytes"] == target.stat().st_size
    with sqlite3.connect(target) as copy:
        assert copy.execute("SELECT level FROM learners").fetchall() == [("A1",)]
    assert not target.with_name("llmll-backup.db.partial").exists()

    # A second backup replaces the first one.
    client.put("/api/learner", json={"level": "A2"})
    assert client.post("/api/backup").status_code == 200
    with sqlite3.connect(target) as copy:
        assert copy.execute("SELECT level FROM learners").fetchall() == [("A2",)]


def test_backup_requires_auth(migrated_settings: Settings, clock: Clock) -> None:
    with make_client(migrated_settings, clock) as c:
        assert c.post("/api/backup").status_code == 401
        auth = {"Authorization": f"Bearer {TOKEN}"}
        assert c.post("/api/backup", headers=auth).status_code == 200


def test_backup_needs_a_database_file(clock: Clock) -> None:
    settings = Settings(database_url="sqlite:///:memory:")
    with TestClient(create_app(settings, now=clock)) as c:
        assert c.post("/api/backup").status_code == 409
