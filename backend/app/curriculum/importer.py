"""Upsert a loaded curriculum into the database."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from wordfreq import zipf_frequency

from app.curriculum.loader import Curriculum, LoadedItem
from app.store.models import Item, ItemPrerequisite, Learner, LearnerItem

USER_SOURCE = "user"


@dataclass
class ImportReport:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    suspended: int = 0


def _zipf(item: LoadedItem) -> float | None:
    if item.kind != "lemma":
        return None
    return float(zipf_frequency(item.payload["lemma"], "de"))


def import_curriculum(session: Session, curriculum: Curriculum, now: datetime) -> ImportReport:
    """Upsert by id. Removed ids are suspended, never deleted. The caller commits."""
    from app.services.learner import initial_status

    report = ImportReport()
    existing = {item.id: item for item in session.scalars(select(Item))}
    learner = session.get(Learner, 1)
    new_items: list[Item] = []
    present: set[str] = set()

    for loaded in curriculum.items:
        present.add(loaded.id)
        row = existing.get(loaded.id)
        if row is None:
            row = Item(
                id=loaded.id,
                kind=loaded.kind,
                cefr_level=loaded.level,
                payload=loaded.payload,
                interference=loaded.interference,
                frequency_zipf=_zipf(loaded),
                source_file=loaded.source_file,
                content_hash=loaded.content_hash,
                suspended=False,
            )
            session.add(row)
            new_items.append(row)
            report.added += 1
        elif row.content_hash != loaded.content_hash or row.suspended:
            row.kind = loaded.kind
            row.cefr_level = loaded.level
            row.payload = loaded.payload
            row.interference = loaded.interference
            row.frequency_zipf = _zipf(loaded)
            row.source_file = loaded.source_file
            row.content_hash = loaded.content_hash
            row.suspended = False
            report.updated += 1
        else:
            report.unchanged += 1

    for item_id, row in existing.items():
        if row.source_file == USER_SOURCE:
            continue  # learner-created items (opt-in while reading) are not curriculum content
        if item_id not in present and not row.suspended:
            row.suspended = True
            report.suspended += 1

    session.flush()
    session.execute(delete(ItemPrerequisite))
    session.add_all(
        ItemPrerequisite(item_id=loaded.id, requires_item_id=req)
        for loaded in curriculum.items
        for req in loaded.requires
    )

    if learner is not None:
        for row in new_items:
            status, source = initial_status(row.kind, row.cefr_level, learner.level)
            session.add(
                LearnerItem(
                    learner_id=learner.id,
                    item_id=row.id,
                    status=status,
                    candidate_source=source,
                    candidate_since=now if status == "candidate" else None,
                )
            )
    session.flush()
    return report
