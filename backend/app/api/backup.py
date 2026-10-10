"""Database backup endpoint."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.store.backup import BackupUnavailable, backup_database

router = APIRouter(prefix="/api", tags=["backup"])


class BackupOut(BaseModel):
    path: str
    size_bytes: int
    created_at: datetime


@router.post("/backup", response_model=BackupOut, operation_id="createBackup")
def create_backup(request: Request) -> BackupOut:
    """Write a consistent copy of the database to `llmll-backup.db` in the data directory."""
    try:
        target = backup_database(request.app.state.settings.database_url)
    except BackupUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    stat = target.stat()
    return BackupOut(
        path=str(target),
        size_bytes=stat.st_size,
        created_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
    )
