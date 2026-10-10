"""Progress review endpoints (design: docs/design/M8-progress.md)."""

from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_now, require_learner
from app.api.items import ItemDetail, Kind
from app.services import progress as service
from app.services.common import SessionError
from app.store.db import get_session
from app.store.models import Learner

router = APIRouter(prefix="/api/progress", tags=["progress"])

MemoryState = Literal["new", "learning", "young", "mature", "presumed_known"]
Outcome = Literal["correct", "assisted", "error"]
ItemSort = Literal["weakest", "strongest", "recent", "due", "errors"]


# --- summary -----------------------------------------------------------------------------------


class StreakOut(BaseModel):
    current: int
    longest: int
    last_study_day: date | None
    studied_today: bool


class TotalsOut(BaseModel):
    reviews: int
    exercises: int
    readings: int
    items_introduced: int
    minutes: float


class PeriodOut(BaseModel):
    """Totals over 7 local days (`this_week`: the last 7 days including today)."""

    study_days: int
    reviews: int
    exercises: int
    readings: int
    new_items: int
    minutes: float


class RetentionOut(BaseModel):
    observed: float | None
    target: float
    n_reviews: int


class StateCountsOut(BaseModel):
    new: int
    learning: int
    young: int
    mature: int
    presumed_known: int


class SummaryOut(BaseModel):
    timezone: str
    today: date
    streak: StreakOut
    study_days_total: int
    totals: TotalsOut
    this_week: PeriodOut
    last_week: PeriodOut
    retention: RetentionOut
    states: StateCountsOut
    states_words: StateCountsOut
    states_grammar: StateCountsOut
    due_now: int
    due_today: int


@router.get("/summary", response_model=SummaryOut, operation_id="getProgressSummary")
def get_summary(
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> Any:
    return service.summary(db, learner, now())


# --- activity, levels, forecast ----------------------------------------------------------------


class DayOut(BaseModel):
    date: date
    reviews: int
    exercises: int
    new_items: int
    readings: int
    minutes: float
    active: bool
    """A study day: an answered card/exercise or a finished reading."""


class WeekAccuracyOut(BaseModel):
    week_start: date
    """Monday of the (local) week."""
    flashcards_correct_rate: float | None
    production_correct_rate: float | None
    flashcards_n: int
    production_n: int
    n: int


class ActivityOut(BaseModel):
    timezone: str
    today: date
    days: list[DayOut]
    """One row per local day, oldest first, empty days included."""
    weeks: list[WeekAccuracyOut]


@router.get("/activity", response_model=ActivityOut, operation_id="getProgressActivity")
def get_activity(
    days: int = Query(140, ge=1, le=service.MAX_ACTIVITY_DAYS),
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> Any:
    return service.activity(db, learner, now(), days)


class LevelProgressOut(BaseModel):
    level: str
    kind: Kind
    total: int
    introduced: int
    new: int
    learning: int
    young: int
    mature: int
    presumed_known: int
    mean_mastery: float | None


@router.get("/levels", response_model=list[LevelProgressOut], operation_id="getProgressLevels")
def get_levels(
    db: Session = Depends(get_session), learner: Learner = Depends(require_learner)
) -> Any:
    return service.levels(db, learner)


class ForecastDayOut(BaseModel):
    date: date
    due: int


@router.get("/forecast", response_model=list[ForecastDayOut], operation_id="getProgressForecast")
def get_forecast(
    days: int = Query(14, ge=1, le=service.MAX_FORECAST_DAYS),
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> Any:
    return service.forecast(db, learner, now(), days)


# --- items -------------------------------------------------------------------------------------


class FacetStateOut(BaseModel):
    facet: str
    mastery: float
    stability: float | None
    due: datetime | None
    n_eff: float
    state: MemoryState


class TagCountOut(BaseModel):
    tag: str
    count: int


class ProgressItemRow(BaseModel):
    id: str
    kind: Kind
    level: str
    label: str
    translation_it: str | None
    status: str | None
    state: MemoryState
    mastery: float | None
    """Mean mastery over the facets; null without memory."""
    n_eff: float
    due: datetime | None
    """Earliest due date over the facets."""
    last_practiced: datetime | None
    errors: int
    answers: int
    """Graded answers (correct + assisted + error), voided events excluded."""
    error_rate: float | None
    top_tags: list[TagCountOut]
    facets: list[FacetStateOut]


class ProgressItemList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ProgressItemRow]


@router.get("/items", response_model=ProgressItemList, operation_id="listProgressItems")
def list_items(
    kind: Kind | None = None,
    sort: ItemSort = "recent",
    q: str | None = None,
    level: str | None = None,
    state: MemoryState | None = None,
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
) -> Any:
    return service.list_items(
        db, learner, kind=kind, sort=sort, q=q, level=level, state=state, limit=limit, offset=offset
    )


class TrajectoryPointOut(BaseModel):
    event_id: int
    ts: datetime
    kind: str
    outcome: Outcome | None
    mastery: float
    stability: float | None


class OutcomeCountsOut(BaseModel):
    correct: int
    assisted: int
    error: int


class FacetDetailOut(BaseModel):
    facet: str
    state: MemoryState
    mastery: float | None = None
    stability: float | None = None
    due: datetime | None = None
    n_eff: float | None = None
    counts: OutcomeCountsOut
    tag_errors: list[TagCountOut]
    trajectory_total: int
    trajectory: list[TrajectoryPointOut]
    """Mastery after each event, oldest first (the last points when there are very many); the
    last point equals the stored memory state."""


class AnswerErrorOut(BaseModel):
    start: int
    end: int
    original: str
    correction: str
    item_id: str | None
    label: str | None = None
    diagnostic_tags: list[str]
    severity: Literal["minor", "major"]
    confidence: float
    explanation: str


class AnswerItemOut(BaseModel):
    item_id: str
    label: str
    outcome: Outcome
    diagnostic_tags: list[str]


class ContestInfoOut(BaseModel):
    id: int
    status: Literal["open", "resolved"]
    verdict: Literal["accepted", "rejected", "partial"] | None
    reason: str | None
    rationale: str
    created_at: datetime


class AnswerCardOut(BaseModel):
    """An answered card or exercise, as it stands now (after any contest)."""

    exercise_id: str
    attempt_id: int
    evaluation_id: int | None
    type: Literal[
        "flashcard_intro",
        "flashcard_recognition",
        "flashcard_production",
        "grammar_intro",
        "production",
    ]
    subtype: Literal["translation", "guided", "transform", "summary", "cloze", "choice"] | None
    answered_at: datetime
    outcome: Outcome | None
    """Null for intro cards."""
    prompt: str
    instructions: str | None
    options: list[str] | None
    """Recognition cards: the options shown."""
    answer: str | None
    expected: str | None
    """Flashcards: the expected answer; production: the corrected sentence."""
    feedback: str | None
    used_hint: bool
    duration_ms: int | None
    errors: list[AnswerErrorOut]
    items: list[AnswerItemOut]
    contest: ContestInfoOut | None
    item_outcome: Outcome | None = None
    """Only in an item's `recent_answers`: the outcome for that item."""
    item_errors: list[AnswerErrorOut] = []
    """Only in an item's `recent_answers`: the errors on that item."""


class ExplanationExampleOut(BaseModel):
    de: str
    translation: str


class CachedExplanationOut(BaseModel):
    evaluation_id: int
    markdown: str
    examples: list[ExplanationExampleOut]
    created_at: datetime


class ProgressItemDetail(BaseModel):
    item: ItemDetail
    state: MemoryState
    mastery: float | None
    counts: OutcomeCountsOut
    tag_errors: list[TagCountOut]
    facets: list[FacetDetailOut]
    recent_answers: list[AnswerCardOut]
    explanation: CachedExplanationOut | None
    practice_queued: bool


@router.get("/items/{item_id}", response_model=ProgressItemDetail, operation_id="getProgressItem")
def get_item(
    item_id: str,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
) -> Any:
    try:
        return service.item_detail(db, learner, item_id)
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


class PracticeOut(BaseModel):
    item_id: str
    queued: bool
    already_queued: bool
    production_slots: int
    """Written exercises per session; 0 means the queue is not used."""


@router.post(
    "/items/{item_id}/practice", response_model=PracticeOut, operation_id="practiceProgressItem"
)
def practice_item(
    item_id: str,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> Any:
    try:
        return service.practice_item(db, learner, item_id, now())
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


# --- history -----------------------------------------------------------------------------------


class HistoryRow(BaseModel):
    kind: Literal["session", "reading"]
    id: str
    """Session id (`session`) or reading session id (`reading`); use both in the detail URL."""
    started_at: datetime
    ended_at: datetime | None
    duration_minutes: float | None
    cards_answered: int
    correct_rate: float | None
    """Share of `correct` among graded cards (intro cards excluded); for a reading, of its
    summary exercise."""
    new_items: int
    title: str | None
    """Readings: the text title."""
    words_looked_up: int | None
    """Readings: distinct words tapped."""


class HistoryList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[HistoryRow]


@router.get("/history", response_model=HistoryList, operation_id="getProgressHistory")
def get_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
) -> Any:
    return service.history(db, learner, limit, offset)


class SessionDetailOut(BaseModel):
    kind: Literal["session"] = "session"
    id: str
    started_at: datetime
    ended_at: datetime | None
    duration_minutes: float | None
    cards_answered: int
    correct_rate: float | None
    new_items: int
    cards: list[AnswerCardOut]
    """In answering order."""


class LookupOut(BaseModel):
    token_index: int | None
    word: str | None
    lemma: str | None
    item_id: str | None
    label: str | None


class ReadingDetailOut(BaseModel):
    kind: Literal["reading"] = "reading"
    id: str
    text_id: int
    title: str
    level: str
    body: str
    source_title: str
    source_url: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_minutes: float | None
    lookups: list[LookupOut]
    summary: AnswerCardOut | None
    """The summary exercise and its evaluation, once answered."""


@router.get(
    "/history/session/{session_id}",
    response_model=SessionDetailOut,
    operation_id="getProgressSession",
)
def get_session_detail(
    session_id: str,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
) -> Any:
    try:
        return service.session_detail(db, learner, session_id)
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.get(
    "/history/reading/{reading_id}",
    response_model=ReadingDetailOut,
    operation_id="getProgressReading",
)
def get_reading_detail(
    reading_id: int,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
) -> Any:
    try:
        return service.reading_detail(db, learner, reading_id)
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
