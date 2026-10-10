"""Flashcard session endpoints."""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_languagetool, get_llm, get_now, require_learner
from app.llm.client import LLMClient
from app.nlp.languagetool import LanguageToolClient
from app.services import drills, production
from app.services import sessions as service
from app.store.db import get_session
from app.store.models import Exercise, Learner

router = APIRouter(prefix="/api", tags=["sessions"])

CardType = Literal[
    "flashcard_intro",
    "flashcard_recognition",
    "flashcard_production",
    "grammar_intro",
    "production",
]


class CardExample(BaseModel):
    de: str
    it: str


class CardPrompt(BaseModel):
    """Prompt shown to the learner. Fields depend on the card type; unused ones are null."""

    de: str | None = None
    it: str | None = None
    pos: str | None = None
    options: list[str] | None = None
    needs_article: bool | None = None
    lemma: str | None = None
    article: str | None = None
    plural: str | None = None
    translation_it: str | None = None
    translation_en: str | None = None
    example: CardExample | None = None
    interference_note: str | None = None
    # production exercises: the generated prompt text (null until prepared); cloze and choice
    # prompts hold one `___` gap, and choice exercises list their `options`
    text: str | None = None
    # grammar_intro cards
    title: str | None = None
    reference_it: str | None = None
    examples: list[CardExample] | None = None


class GlossaryEntry(BaseModel):
    item_id: str
    de: str
    translation: str


class SessionCard(BaseModel):
    """A card of the session.

    Every card carries a `CardPrompt` in `prompt`. Production exercises put the generated text in
    `prompt.text` (null until `prepare` has run) and also set `subtype`, `instructions`,
    `glossary` and `item_ids`; `item_id` is their primary target.
    """

    exercise_id: str
    type: CardType
    item_id: str | None = None
    prompt: CardPrompt = Field(default_factory=CardPrompt)
    hint: str | None = None
    status: Literal["pending", "ready", "answered", "failed"] = "ready"
    subtype: Literal["translation", "guided", "transform", "summary", "cloze", "choice"] | None = (
        None
    )
    instructions: str | None = None
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)


class PreparedCard(SessionCard):
    """Result of `prepare`; `fallback_cards` are flashcard intros when generation failed."""

    fallback_cards: list[SessionCard] = Field(default_factory=list)


class SessionOut(BaseModel):
    session_id: str
    cards: list[SessionCard]


class AnswerPayload(BaseModel):
    choice: int | None = None
    text: str | None = None


class AnswerIn(BaseModel):
    exercise_id: str
    answer: AnswerPayload = Field(default_factory=AnswerPayload)
    used_hint: bool = False
    duration_ms: int | None = Field(default=None, ge=0)


class ExpectedAnswer(BaseModel):
    text: str
    lemma: str
    article: str | None = None
    plural: str | None = None
    translation_it: str
    example: CardExample | None = None
    correct_index: int | None = None
    """Recognition cards: index of the correct option in `prompt.options`."""


class MemoryOut(BaseModel):
    facet: str
    due: datetime | None
    mastery: float


class AnswerOut(BaseModel):
    kind: Literal["flashcard"] = "flashcard"
    outcome: Literal["correct", "assisted", "error"]
    expected: ExpectedAnswer | None
    diagnostic_tags: list[str]
    feedback_it: str
    memory: MemoryOut | None
    evaluation_id: int | None = None
    """Evaluation to contest (null for intro cards)."""
    attempt_id: int | None = None
    """Attempt id; contest via `/api/attempts/{id}/contest` when `evaluation_id` is null."""


class AnswerError(BaseModel):
    start: int
    end: int
    original: str
    correction: str
    item_id: str | None
    diagnostic_tags: list[str]
    severity: Literal["minor", "major"]
    confidence: float
    explanation: str


class ItemResult(BaseModel):
    item_id: str
    label: str
    outcome: Literal["correct", "assisted", "error"]
    needs_remediation: bool


class ProductionAnswerOut(BaseModel):
    kind: Literal["production"] = "production"
    outcome: Literal["correct", "minor_errors", "major_errors", "off_task"]
    corrected_sentence: str
    errors: list[AnswerError]
    feedback: str
    items: list[ItemResult]
    evaluation_id: int
    attempt_id: int | None = None


def to_card(c: service.BuiltCard) -> SessionCard:
    if isinstance(c.prompt, dict):
        prompt = CardPrompt.model_validate(c.prompt)
    else:
        prompt = CardPrompt(text=c.prompt, options=c.options or None)
    return SessionCard(
        exercise_id=c.exercise_id,
        type=c.type,  # type: ignore[arg-type]
        item_id=c.item_id,
        prompt=prompt,
        hint=c.hint,
        status=c.status,  # type: ignore[arg-type]
        subtype=c.subtype,  # type: ignore[arg-type]
        instructions=c.instructions,
        glossary=[GlossaryEntry(**g) for g in c.glossary],
        item_ids=c.item_ids,
    )


@router.post(
    "/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSession",
)
def create_session(
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> SessionOut:
    session_id, cards = service.build_session(db, learner, now())
    return SessionOut(session_id=session_id, cards=[to_card(c) for c in cards])


class DrillIn(BaseModel):
    item_id: str


@router.post(
    "/drills",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="createDrill",
)
def create_drill(
    body: DrillIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> SessionOut:
    """A drill on one grammar point or construction: its intro card when it is new, then
    `drill_size` exercises (multiple choice, cloze, then open ones), prepared like any other."""
    try:
        session_id, cards = drills.build_drill(db, learner, body.item_id, now())
    except service.SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return SessionOut(session_id=session_id, cards=[to_card(c) for c in cards])


@router.post(
    "/exercises/{exercise_id}/prepare",
    response_model=PreparedCard,
    operation_id="prepareExercise",
)
def prepare_exercise(
    exercise_id: str,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    llm: LLMClient = Depends(get_llm),
    now: Callable[[], datetime] = Depends(get_now),
) -> PreparedCard:
    try:
        exercise, fallback = production.prepare_exercise(db, learner, exercise_id, llm, now())
    except service.SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    card = to_card(production.production_card(exercise))
    return PreparedCard(**card.model_dump(), fallback_cards=[to_card(c) for c in fallback])


@router.post(
    "/sessions/{session_id}/answers",
    response_model=AnswerOut | ProductionAnswerOut,
    operation_id="submitAnswer",
)
def submit_answer(
    session_id: str,
    body: AnswerIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
    llm: LLMClient = Depends(get_llm),
    languagetool: LanguageToolClient | None = Depends(get_languagetool),
) -> Any:
    try:
        exercise = db.get(Exercise, body.exercise_id)
        if exercise is not None and exercise.type == "production":
            service.load_open_exercise(db, learner, session_id, body.exercise_id)
            return production.submit_production_answer(
                db,
                learner,
                exercise,
                body.answer.model_dump(exclude_none=True),
                body.used_hint,
                body.duration_ms,
                now(),
                llm,
                languagetool,
            )
        return service.submit_answer(
            db,
            learner,
            session_id,
            body.exercise_id,
            body.answer.model_dump(exclude_none=True),
            body.used_hint,
            body.duration_ms,
            now(),
        )
    except service.SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
