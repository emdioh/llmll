"""Building flashcard sessions and processing answers."""

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fsrs import Card
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.curriculum.schema import CEFR_LEVELS
from app.domain.answers import (
    article_for,
    check_production,
    check_recognition,
    display_form,
)
from app.domain.budget import new_item_budget
from app.domain.config import ProjectionConfig
from app.domain.selection import MemoryView, select_due
from app.services.learner import projection_config, settings_of
from app.store.events import append_event
from app.store.models import (
    Attempt,
    Exercise,
    Item,
    ItemMemory,
    Learner,
    LearnerItem,
)

GENERATOR = "flashcards.v1"
SOURCE_PRIORITY = {"optin": 0, "article": 1, "wordlist": 2}
INTRO_EVERY = 3
FACETS = ("recognition", "production")
GENDER_IT = {"m": "maschile", "f": "femminile", "n": "neutro"}


class SessionError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class BuiltCard:
    exercise_id: str
    type: str
    item_id: str
    prompt: dict[str, Any]
    hint: str | None


def _gender_of(payload: dict[str, Any]) -> str | None:
    if payload.get("plural_only"):
        return "pl"
    return payload.get("gender")


def _form(payload: dict[str, Any]) -> str:
    return display_form(payload["lemma"], payload.get("gender"), payload.get("plural_only", False))


def _solution_info(item: Item) -> dict[str, Any]:
    p = item.payload
    return {
        "lemma": p["lemma"],
        "gender": _gender_of(p),
        "text": _form(p),
        "article": article_for(p.get("gender"), p.get("plural_only", False)),
        "plural": p.get("plural"),
        "translation_it": p["translations"]["it"],
        "example": p.get("example"),
        "interference_note": interference_note(item),
    }


def interference_note(item: Item) -> str | None:
    inter = item.interference or {}
    parts = []
    it_gender = inter.get("it_gender")
    gender = item.payload.get("gender")
    if it_gender and gender and it_gender != gender:
        parts.append(
            f"In italiano è {GENDER_IT[it_gender]}, in tedesco è {GENDER_IT[gender]} "
            f"(«{article_for(gender)}»)."
        )
    friend = inter.get("false_friend")
    if friend:
        parts.append(friend["note_it"])
    if inter.get("note_it"):
        parts.append(inter["note_it"])
    return " ".join(parts) or None


def _distractors(
    session: Session, item: Item, exercise_id: str, correct: str, count: int = 3
) -> list[str]:
    p = item.payload
    rank = CEFR_LEVELS.index(item.cefr_level)
    near = {CEFR_LEVELS[i] for i in (rank - 1, rank, rank + 1) if 0 <= i < len(CEFR_LEVELS)}
    others = session.scalars(
        select(Item)
        .where(Item.kind == "lemma", Item.id != item.id, ~Item.suspended)
        .order_by(Item.id)
    ).all()
    correct_key = correct.casefold()

    rng = random.Random(exercise_id)

    def pick(pool: list[Item]) -> list[str]:
        seen, texts = {correct_key}, []
        pool = rng.sample(pool, len(pool))
        for other in pool:
            text = other.payload["translations"]["it"]
            if text.casefold() not in seen:
                seen.add(text.casefold())
                texts.append(text)
        return texts

    same_pos = [o for o in others if o.payload["pos"] == p["pos"]]
    tiers = [
        [o for o in same_pos if o.cefr_level in near],
        same_pos,
        [o for o in others if o.cefr_level in near],
        others,
    ]
    chosen: list[str] = []
    for tier in tiers:
        for text in pick(tier):
            if text not in chosen and len(chosen) < count:
                chosen.append(text)
        if len(chosen) >= count:
            break
    return chosen


def _make_exercise(
    learner_id: int,
    session_id: str,
    exercise_id: str,
    type_: str,
    item: Item,
    facet: str,
    prompt: dict[str, Any],
    solution: dict[str, Any],
    weight: float,
    now: datetime,
) -> Exercise:
    return Exercise(
        id=exercise_id,
        learner_id=learner_id,
        type=type_,
        prompt=prompt,
        solution=solution,
        targets=[{"item_id": item.id, "facet": facet, "weight": weight}],
        generator=GENERATOR,
        created_at=now,
        session_id=session_id,
    )


def _review_card(
    db: Session,
    learner_id: int,
    session_id: str,
    item: Item,
    facet: str,
    cfg: ProjectionConfig,
    now: datetime,
) -> tuple[Exercise, BuiltCard]:
    exercise_id = uuid.uuid4().hex
    info = _solution_info(item)
    hint: str | None = None
    if facet == "recognition":
        type_ = "flashcard_recognition"
        correct = info["translation_it"]
        options = [correct, *_distractors(db, item, exercise_id, correct)]
        random.Random(exercise_id).shuffle(options)
        prompt: dict[str, Any] = {"de": info["text"], "options": options}
        solution = {**info, "correct_index": options.index(correct)}
        weight = cfg.weight("flashcard_recognition")
    else:
        type_ = "flashcard_production"
        needs_article = info["gender"] is not None
        prompt = {
            "it": info["translation_it"],
            "pos": item.payload["pos"],
            "needs_article": needs_article,
        }
        lemma = info["lemma"]
        hint = f"Inizia con «{lemma[0]}», {len(lemma)} lettere"
        if needs_article:
            hint += " (articolo escluso)"
        solution = info
        weight = cfg.weight("flashcard_production")
    exercise = _make_exercise(
        learner_id, session_id, exercise_id, type_, item, facet, prompt, solution, weight, now
    )
    return exercise, BuiltCard(exercise_id, type_, item.id, prompt, hint)


def _intro_card(
    learner_id: int, session_id: str, item: Item, now: datetime
) -> tuple[Exercise, BuiltCard]:
    exercise_id = uuid.uuid4().hex
    info = _solution_info(item)
    p = item.payload
    prompt = {
        "de": info["text"],
        "lemma": info["lemma"],
        "pos": p["pos"],
        "article": info["article"],
        "plural": info["plural"],
        "translation_it": info["translation_it"],
        "translation_en": p["translations"]["en"],
        "example": info["example"],
        "interference_note": info["interference_note"],
    }
    exercise = _make_exercise(
        learner_id,
        session_id,
        exercise_id,
        "flashcard_intro",
        item,
        "recognition",
        prompt,
        info,
        0.0,
        now,
    )
    exercise.targets = [{"item_id": item.id, "facet": facet, "weight": 0.0} for facet in FACETS]
    return exercise, BuiltCard(exercise_id, "flashcard_intro", item.id, prompt, None)


def build_session(db: Session, learner: Learner, now: datetime) -> tuple[str, list[BuiltCard]]:
    settings = settings_of(learner)
    cfg = projection_config(settings)
    session_id = uuid.uuid4().hex

    # 1. Due reviews (lemmas only in M1), at most one card per item.
    rows = db.execute(
        select(ItemMemory, Item)
        .join(Item, Item.id == ItemMemory.item_id)
        .where(
            ItemMemory.learner_id == learner.id,
            ItemMemory.due.is_not(None),
            ItemMemory.due <= now,
            Item.kind == "lemma",
            ~Item.suspended,
        )
    ).all()
    items_by_id = {item.id: item for _, item in rows}
    views = [
        MemoryView(
            item_id=mem.item_id,
            facet=mem.facet,
            due=mem.due,
            card=Card.from_dict(mem.fsrs_card) if mem.fsrs_card else None,
            frequency_zipf=item.frequency_zipf,
        )
        for mem, item in rows
    ]
    ordered = select_due(views, now, limit=len(views), desired_retention=cfg.desired_retention)
    due_cards: list[tuple[str, str]] = []
    for view in ordered:
        if all(view.item_id != i for i, _ in due_cards):
            due_cards.append((view.item_id, view.facet))
    backlog = len(due_cards)
    due_cards = due_cards[: settings.review_cap]

    # 2. New lemmas within the budget.
    since = now - timedelta(days=7)
    introduced = dict(
        db.execute(
            select(Item.kind, func.count())
            .join(LearnerItem, LearnerItem.item_id == Item.id)
            .where(LearnerItem.learner_id == learner.id, LearnerItem.introduced_at >= since)
            .group_by(Item.kind)
        ).all()
    )
    budget = new_item_budget(
        introduced.get("lemma", 0), introduced.get("grammar", 0), backlog, settings
    )
    candidates = db.execute(
        select(LearnerItem, Item)
        .join(Item, Item.id == LearnerItem.item_id)
        .where(
            LearnerItem.learner_id == learner.id,
            LearnerItem.status == "candidate",
            Item.kind == "lemma",
            ~Item.suspended,
        )
    ).all()
    candidates.sort(
        key=lambda r: (
            SOURCE_PRIORITY.get(r[0].candidate_source or "wordlist", 9),
            CEFR_LEVELS.index(r[1].cefr_level),
            -(r[1].frequency_zipf or 0.0),
            r[1].id,
        )
    )
    new_items = [item for _, item in candidates[: min(budget.lemmas, settings.new_per_session)]]

    # 3. Build exercises, interleaving one intro every INTRO_EVERY reviews.
    reviews = []
    for item_id, facet in due_cards:
        reviews.append(
            _review_card(db, learner.id, session_id, items_by_id[item_id], facet, cfg, now)
        )
    intros = [_intro_card(learner.id, session_id, item, now) for item in new_items]
    sequence: list[tuple[Exercise, BuiltCard]] = []
    intro_iter = iter(intros)
    for index, review_card in enumerate(reviews, 1):
        sequence.append(review_card)
        if index % INTRO_EVERY == 0 and (nxt := next(intro_iter, None)):
            sequence.append(nxt)
    sequence.extend(intro_iter)

    db.add_all(ex for ex, _ in sequence)
    db.commit()
    return session_id, [card for _, card in sequence]


def _feedback(
    outcome: str, tags: list[str], used_hint: bool, solution: dict[str, Any], kind: str
) -> str:
    text = solution["text"]
    gender = solution["gender"]
    if kind == "flashcard_intro":
        return f"Nuova parola: {text}. La rivedrai presto."
    if kind == "flashcard_recognition":
        if outcome == "correct":
            return f"Corretto! {text} = {solution['translation_it']}."
        return f"La risposta corretta è «{solution['translation_it']}» ({text})."
    if outcome == "correct":
        return f"Corretto! {text}."
    if outcome == "assisted":
        if "spelling" in tags:
            return f"Quasi: si scrive «{text}»."
        return f"Corretto, ma con il suggerimento: {text}."
    if "gender" in tags:
        feedback = (
            f"Attenzione: in tedesco è *{solution['article']}* {solution['lemma']} "
            f"({GENDER_IT.get(gender, 'plurale')})."
        )
        if solution.get("interference_note"):
            feedback += " " + solution["interference_note"]
        return feedback
    if "article_missing" in tags:
        return f"Ricorda l'articolo: *{solution['article']}* {solution['lemma']}."
    return f"La risposta corretta è: {text}."


def _memory_out(row: ItemMemory | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"facet": row.facet, "due": row.due, "mastery": row.mastery}


def submit_answer(
    db: Session,
    learner: Learner,
    session_id: str,
    exercise_id: str,
    answer: dict[str, Any],
    used_hint: bool,
    duration_ms: int | None,
    now: datetime,
) -> dict[str, Any]:
    exercise = db.get(Exercise, exercise_id)
    if exercise is None or exercise.session_id != session_id or exercise.learner_id != learner.id:
        raise SessionError(404, "Exercise not found in this session")
    if db.scalar(select(Attempt.id).where(Attempt.exercise_id == exercise_id)) is not None:
        raise SessionError(409, "Exercise already answered")

    cfg = projection_config(settings_of(learner))
    solution = exercise.solution
    target = exercise.targets[0]
    item_id = target["item_id"]
    learner_item = db.get(LearnerItem, (learner.id, item_id))
    tags: list[str] = []
    memory_row: ItemMemory | None = None

    if exercise.type == "flashcard_intro":
        outcome = "correct"
    elif exercise.type == "flashcard_recognition":
        outcome = check_recognition(answer.get("choice"), solution["correct_index"])
    else:
        outcome, tags = check_production(
            answer.get("text") or "", solution["lemma"], solution["gender"]
        )
        if used_hint and outcome == "correct":
            outcome = "assisted"

    attempt = Attempt(
        exercise_id=exercise_id,
        answer=answer,
        used_hint=used_hint,
        duration_ms=duration_ms,
        outcome=outcome,
        submitted_at=now,
    )
    db.add(attempt)
    db.flush()
    common = {
        "learner_id": learner.id,
        "item_id": item_id,
        "ts": now,
        "exercise_id": exercise_id,
        "attempt_id": attempt.id,
    }

    if exercise.type == "flashcard_intro":
        for facet in FACETS:
            _, row = append_event(db, cfg, facet=facet, kind="introduce", **common)
            if facet == "production":
                memory_row = row
        if learner_item is not None:
            learner_item.status = "introduced"
            learner_item.introduced_at = learner_item.introduced_at or now
    else:
        facet = target["facet"]
        was_presumed = learner_item is not None and learner_item.status == "presumed_known"
        _, memory_row = append_event(
            db,
            cfg,
            facet=facet,
            kind="review",
            outcome=outcome,
            evidence_weight=target["weight"],
            diagnostic_tags=tags,
            presumed_known=was_presumed,
            **common,
        )
        if learner_item is not None and learner_item.status != "introduced":
            learner_item.status = "introduced"
            learner_item.introduced_at = learner_item.introduced_at or now
            if was_presumed:
                append_event(
                    db, cfg, facet=facet, kind="status_change", presumed_known=True, **common
                )
    db.commit()

    return {
        "outcome": outcome,
        "expected": {
            "text": solution["text"],
            "lemma": solution["lemma"],
            "article": solution["article"],
            "plural": solution["plural"],
            "translation_it": solution["translation_it"],
            "example": solution["example"],
        },
        "diagnostic_tags": tags,
        "feedback_it": _feedback(outcome, tags, used_hint, solution, exercise.type),
        "memory": _memory_out(memory_row),
    }
