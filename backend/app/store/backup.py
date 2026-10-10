"""Consistent copies of the SQLite database."""

import os
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url

BACKUP_NAME = "llmll-backup.db"


class BackupUnavailable(Exception):
    """The database is not a SQLite file, so there is nothing to copy."""


def database_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise BackupUnavailable("backups are only supported for a SQLite database file")
    return Path(url.database)


def backup_database(database_url: str) -> Path:
    """Copy the database to `llmll-backup.db` next to it, replacing any previous backup.

    Uses SQLite's online backup API, so it is safe while the app is running. The copy is written
    to a temporary file first, so an interrupted backup never leaves a truncated file behind.
    """
    source = database_path(database_url)
    target = source.with_name(BACKUP_NAME)
    partial = target.with_name(BACKUP_NAME + ".partial")
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(partial)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.replace(partial, target)
    return target
