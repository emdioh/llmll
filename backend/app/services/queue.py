"""Candidate queue, weekly budget and opt-in/opt-out (design: M4 §1)."""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.curriculum.schema import CEFR_LEVELS
from app.domain.budget import (
    LearnerItemView,
    NewItemBudget,
    QueueItem,
    candidate_queue,
    new_item_budget,
    queue_source,
)
from app.services.common import SessionError
from app.services.learner import settings_of
from app.store.models import Item, ItemMemory, ItemPrerequisite, Learner, LearnerItem

GRAMMAR_KINDS = ("grammar", "construction")


def backlog(db: Session, learner_id: int, now: datetime) -> int:
    """Number of memories (item, facet) whose review is due; shared by the budget and sessions."""
    return (
        db.scalar(
            select(func.count())
            .select_from(ItemMemory)
            .join(Item, Item.id == ItemMemory.item_id)
            .where(
                ItemMemory.learner_id == learner_id,
                ItemMemory.due.is_not(None),
                ItemMemory.due <= now,
                ~Item.suspended,
            )
        )
        or 0
    )


def weekly_budget(db: Session, learner: Learner, now: datetime) -> tuple[NewItemBudget, int]:
    """`(budget, backlog)`: new items still allowed this week (M1 §3.6)."""
    since = now - timedelta(days=7)
    introduced: dict[str, int] = defaultdict(int)
    for kind, count in db.execute(
        select(Item.kind, func.count())
        .join(LearnerItem, LearnerItem.item_id == Item.id)
        .where(LearnerItem.learner_id == learner.id, LearnerItem.introduced_at >= since)
        .group_by(Item.kind)
    ):
        introduced["grammar" if kind in GRAMMAR_KINDS else kind] += count
    pending = backlog(db, learner.id, now)
    return (
        new_item_budget(introduced["lemma"], introduced["grammar"], pending, settings_of(learner)),
        pending,
    )


def candidate_items(db: Session, learner: Learner, now: datetime) -> list[tuple[Item, str]]:
    """The queue of items to introduce, best first, with the source of each."""
    items = db.scalars(select(Item).where(~Item.suspended)).all()
    states = {
        row.item_id: row
        for row in db.scalars(select(LearnerItem).where(LearnerItem.learner_id == learner.id))
    }
    requires: defaultdict[str, list[str]] = defaultdict(list)
    for item_id, required in db.execute(
        select(ItemPrerequisite.item_id, ItemPrerequisite.requires_item_id)
    ):
        requires[item_id].append(required)
    grammar_order = {
        item.id: n
        for n, item in enumerate(
            sorted(
                (i for i in items if i.kind in GRAMMAR_KINDS),
                key=lambda i: (CEFR_LEVELS.index(i.cefr_level), i.kind != "grammar", i.id),
            )
        )
    }
    views = {
        item_id: LearnerItemView(row.status, row.candidate_source)
        for item_id, row in states.items()
    }
    queue_items = [
        QueueItem(
            item_id=i.id,
            kind=i.kind,
            level=i.cefr_level,
            frequency_zipf=i.frequency_zipf,
            order=grammar_order.get(i.id, 0),
            requires=tuple(requires.get(i.id, ())),
        )
        for i in items
    ]
    ids = candidate_queue(views, queue_items, now, max_level=learner.level)
    by_id = {i.id: i for i in items}
    return [
        (
            by_id[item_id],
            queue_source(
                by_id[item_id].kind, views[item_id], learner.level, by_id[item_id].cefr_level
            )
            or "wordlist",
        )
        for item_id in ids
    ]


def optin(db: Session, learner: Learner, item_id: str, now: datetime) -> LearnerItem:
    """Make an item an explicit candidate (source `optin`); reverses `optout`."""
    row = _learner_item(db, learner, item_id)
    if row.status in ("introduced", "presumed_known"):
        return row
    if row.status != "candidate":
        row.candidate_since = now
    row.status = "candidate"
    row.candidate_source = "optin"
    db.commit()
    return row


def optout(db: Session, learner: Learner, item_id: str) -> LearnerItem:
    """Suspend an item that has not been introduced yet."""
    row = _learner_item(db, learner, item_id)
    if row.status in ("introduced", "presumed_known"):
        raise SessionError(409, f"Item is already {row.status}; it cannot be opted out")
    row.status = "suspended"
    row.candidate_source = None
    row.candidate_since = None
    db.commit()
    return row


def _learner_item(db: Session, learner: Learner, item_id: str) -> LearnerItem:
    row = db.get(LearnerItem, (learner.id, item_id))
    if row is None:
        raise SessionError(404, "Item not found")
    return row
