"""Read access to items, grammar and the learner's state for them."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.curriculum.schema import CEFR_LEVELS
from app.domain.answers import display_form
from app.store.models import Item, ItemMemory, ItemPrerequisite, LearnerItem


def item_label(item: Item) -> str:
    p = item.payload
    if item.kind == "lemma":
        return display_form(p["lemma"], p.get("gender"), p.get("plural_only", False))
    if item.kind == "construction":
        return p["pattern"]
    return p["title_it"]


def item_translation_it(item: Item) -> str | None:
    translations = item.payload.get("translations")
    return translations["it"] if translations else None


@dataclass
class ItemFilter:
    kind: str | None = None
    level: str | None = None
    status: str | None = None
    q: str | None = None


def _memories(
    session: Session, learner_id: int, item_ids: list[str]
) -> dict[str, list[ItemMemory]]:
    result: dict[str, list[ItemMemory]] = {}
    if not item_ids:
        return result
    for row in session.scalars(
        select(ItemMemory)
        .where(ItemMemory.learner_id == learner_id, ItemMemory.item_id.in_(item_ids))
        .order_by(ItemMemory.item_id, ItemMemory.facet)
    ):
        result.setdefault(row.item_id, []).append(row)
    return result


def list_items(
    session: Session, learner_id: int | None, flt: ItemFilter, limit: int, offset: int
) -> tuple[int, list[dict[str, Any]]]:
    stmt = select(Item, LearnerItem.status).outerjoin(
        LearnerItem,
        (LearnerItem.item_id == Item.id) & (LearnerItem.learner_id == (learner_id or 0)),
    )
    if flt.kind:
        stmt = stmt.where(Item.kind == flt.kind)
    if flt.level:
        stmt = stmt.where(Item.cefr_level == flt.level)
    if flt.status:
        stmt = stmt.where(LearnerItem.status == flt.status)
    rows = [(item, status) for item, status in session.execute(stmt) if _matches_query(item, flt.q)]
    rows.sort(
        key=lambda r: (CEFR_LEVELS.index(r[0].cefr_level), -(r[0].frequency_zipf or 0), r[0].id)
    )
    page = rows[offset : offset + limit]
    memories = _memories(session, learner_id, [i.id for i, _ in page]) if learner_id else {}
    items = [
        {
            "id": item.id,
            "kind": item.kind,
            "level": item.cefr_level,
            "label": item_label(item),
            "translation_it": item_translation_it(item),
            "status": status,
            "memory": [memory_info(m) for m in memories.get(item.id, [])],
        }
        for item, status in page
    ]
    return len(rows), items


def _matches_query(item: Item, q: str | None) -> bool:
    if not q:
        return True
    needle = q.casefold()
    haystacks = [item_label(item), item_translation_it(item) or "", item.id]
    translations = item.payload.get("translations") or {}
    haystacks.append(translations.get("en", ""))
    return any(needle in h.casefold() for h in haystacks)


def memory_info(row: ItemMemory) -> dict[str, Any]:
    return {
        "facet": row.facet,
        "due": row.due,
        "mastery": row.mastery,
        "stability": row.stability,
    }


def get_item(session: Session, learner_id: int | None, item_id: str) -> dict[str, Any] | None:
    item = session.get(Item, item_id)
    if item is None:
        return None
    learner_item = session.get(LearnerItem, (learner_id, item_id)) if learner_id else None
    requires = list(
        session.scalars(
            select(ItemPrerequisite.requires_item_id)
            .where(ItemPrerequisite.item_id == item_id)
            .order_by(ItemPrerequisite.requires_item_id)
        )
    )
    memories = _memories(session, learner_id, [item_id]).get(item_id, []) if learner_id else []
    return {
        "id": item.id,
        "kind": item.kind,
        "level": item.cefr_level,
        "label": item_label(item),
        "translation_it": item_translation_it(item),
        "payload": item.payload,
        "interference": item.interference,
        "requires": requires,
        "frequency_zipf": item.frequency_zipf,
        "suspended": item.suspended,
        "status": learner_item.status if learner_item else None,
        "candidate_source": learner_item.candidate_source if learner_item else None,
        "introduced_at": learner_item.introduced_at if learner_item else None,
        "memory": [memory_info(m) for m in memories],
    }


def list_grammar(session: Session) -> list[Item]:
    items = session.scalars(select(Item).where(Item.kind == "grammar", ~Item.suspended)).all()
    return sorted(items, key=lambda i: (CEFR_LEVELS.index(i.cefr_level), i.id))
