"""Reading endpoints: texts, reading sessions, gloss, opt-in, finish (design: M3 §5)."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_analyzer, get_fetcher, get_llm, get_now, require_learner
from app.api.sessions import SessionCard, to_card
from app.llm.client import LLMClient
from app.nlp.analyzer import Analyzer, AnalyzerUnavailable
from app.nlp.extract import ExtractedText, ExtractionFailed
from app.services import production, reading
from app.services.common import SessionError
from app.store.db import get_session
from app.store.models import Learner, ReadingSession, SourceText, TextVersion

router = APIRouter(prefix="/api", tags=["reading"])

WordClass = Literal[
    "ignore",
    "known",
    "presumed_known",
    "auto_candidate",
    "optin",
    "optin_unlisted",
    "ignore_compound",
]


class CreateTextIn(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    text: str | None = None
    title: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "CreateTextIn":
        if bool(self.url and self.url.strip()) == bool(self.text and self.text.strip()):
            raise ValueError("provide either `url` or `text`")
        return self


class GenerateTextIn(BaseModel):
    topic: str | None = Field(default=None, max_length=300)


class TokenOut(BaseModel):
    i: int
    start: int
    end: int
    lemma: str
    is_alpha: bool
    word_class: WordClass
    item_id: str | None
    parts: list[str]


class NewWordOut(BaseModel):
    lemma: str
    translation: str


class VersionOut(BaseModel):
    id: int
    level: str
    title: str
    body: str
    coverage: float
    attempt: int
    tokens: list[TokenOut]
    new_words: list[NewWordOut]
    notes: str


class TextOut(BaseModel):
    id: int
    source_title: str
    source_url: str | None
    source_language: str
    original: str
    created_at: datetime
    version: VersionOut


class TextSummary(BaseModel):
    id: int
    title: str
    source_title: str
    source_url: str | None
    level: str
    coverage: float
    created_at: datetime
    reading_count: int


class ReadingOut(BaseModel):
    id: int
    text_version_id: int
    started_at: datetime


class GlossIn(BaseModel):
    token_index: int = Field(ge=0)


class GlossOut(BaseModel):
    translation: str
    lemma: str
    pos: str | None
    gender: str | None
    plural: str | None
    note: str | None
    source: Literal["lexicon", "llm"]
    item_id: str | None
    word_class: WordClass
    can_optin: bool


class OptinOut(BaseModel):
    item_id: str
    label: str
    status: str
    created: bool


class FinishOut(BaseModel):
    session_id: str
    exercise: SessionCard
    implicit_events: int
    candidates: list[str]


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    except ExtractionFailed as exc:
        raise HTTPException(status_code=422, detail=exc.reason) from None
    except AnalyzerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _version_out(version: TextVersion) -> VersionOut:
    analysis = version.analysis
    return VersionOut(
        id=version.id,
        level=version.level,
        title=version.title,
        body=version.body,
        coverage=version.coverage,
        attempt=version.attempt,
        tokens=[
            TokenOut(
                i=t["i"],
                start=t["start"],
                end=t["end"],
                lemma=t["lemma"],
                is_alpha=t["is_alpha"],
                word_class=t["class"],
                item_id=t["item_id"],
                parts=t.get("parts", []),
            )
            for t in analysis["tokens"]
        ],
        new_words=[NewWordOut(**w) for w in analysis.get("new_words", [])],
        notes=analysis.get("notes", ""),
    )


def _text_out(text: SourceText, version: TextVersion) -> TextOut:
    return TextOut(
        id=text.id,
        source_title=text.source_title,
        source_url=text.source_url,
        source_language=text.source_language,
        original=text.source_text,
        created_at=text.created_at,
        version=_version_out(version),
    )


@router.post(
    "/texts", response_model=TextOut, status_code=status.HTTP_201_CREATED, operation_id="createText"
)
def create_text(
    body: CreateTextIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    llm: LLMClient = Depends(get_llm),
    analyzer: Analyzer = Depends(get_analyzer),
    fetch: Callable[[str], ExtractedText] = Depends(get_fetcher),
    now: Callable[[], datetime] = Depends(get_now),
) -> TextOut:
    with _errors():
        if body.url and body.url.strip():
            article = fetch(body.url.strip())
            title, source, url = body.title or article.title, article.text, article.url
        else:
            title, source, url = body.title or "", body.text or "", None
            title = title or " ".join(source.split()[:8])
        text, version = reading.create_text(
            db,
            learner,
            title=title,
            source_text=source,
            source_url=url,
            llm=llm,
            analyzer=analyzer,
            now=now(),
        )
    return _text_out(text, version)


@router.post(
    "/texts/generate",
    response_model=TextOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="generateText",
)
def generate_text(
    body: GenerateTextIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    llm: LLMClient = Depends(get_llm),
    analyzer: Analyzer = Depends(get_analyzer),
    now: Callable[[], datetime] = Depends(get_now),
) -> TextOut:
    with _errors():
        text, version = reading.generate_text(db, learner, body.topic, llm, analyzer, now())
    return _text_out(text, version)


@router.get("/texts", response_model=list[TextSummary], operation_id="listTexts")
def list_texts(
    db: Session = Depends(get_session), learner: Learner = Depends(require_learner)
) -> list[TextSummary]:
    rows = db.execute(
        select(SourceText, TextVersion)
        .join(TextVersion, TextVersion.text_id == SourceText.id)
        .where(SourceText.learner_id == learner.id, TextVersion.selected)
        .order_by(SourceText.created_at.desc(), SourceText.id.desc())
    ).all()
    counts = dict(
        db.execute(
            select(TextVersion.text_id, func.count(ReadingSession.id))
            .join(ReadingSession, ReadingSession.text_version_id == TextVersion.id)
            .group_by(TextVersion.text_id)
        ).all()
    )
    return [
        TextSummary(
            id=text.id,
            title=version.title,
            source_title=text.source_title,
            source_url=text.source_url,
            level=version.level,
            coverage=version.coverage,
            created_at=text.created_at,
            reading_count=counts.get(text.id, 0),
        )
        for text, version in rows
    ]


@router.get("/texts/{text_id}", response_model=TextOut, operation_id="getText")
def get_text(
    text_id: int, db: Session = Depends(get_session), learner: Learner = Depends(require_learner)
) -> TextOut:
    with _errors():
        text = reading.get_text(db, learner, text_id)
        version = reading.selected_version(db, text_id)
    return _text_out(text, version)


@router.post(
    "/texts/{text_id}/reading",
    response_model=ReadingOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="startReading",
)
def start_reading(
    text_id: int,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> ReadingOut:
    with _errors():
        session = reading.start_reading(db, learner, text_id, now())
    return ReadingOut(
        id=session.id, text_version_id=session.text_version_id, started_at=session.started_at
    )


@router.post("/reading/{reading_id}/gloss", response_model=GlossOut, operation_id="glossToken")
def gloss_token(
    reading_id: int,
    body: GlossIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    llm: LLMClient = Depends(get_llm),
    now: Callable[[], datetime] = Depends(get_now),
) -> GlossOut:
    with _errors():
        result = reading.gloss_token(db, learner, reading_id, body.token_index, llm, now())
    return GlossOut(**result)


@router.post("/reading/{reading_id}/optin", response_model=OptinOut, operation_id="optinToken")
def optin_token(
    reading_id: int,
    body: GlossIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    llm: LLMClient = Depends(get_llm),
    now: Callable[[], datetime] = Depends(get_now),
) -> OptinOut:
    with _errors():
        result = reading.optin_token(db, learner, reading_id, body.token_index, llm, now())
    return OptinOut(**result)


@router.post("/reading/{reading_id}/finish", response_model=FinishOut, operation_id="finishReading")
def finish_reading(
    reading_id: int,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> FinishOut:
    with _errors():
        result = reading.finish_reading(db, learner, reading_id, now())
    return FinishOut(
        session_id=result["session_id"],
        exercise=to_card(production.production_card(result["exercise"])),
        implicit_events=result["implicit_events"],
        candidates=result["candidates"],
    )
