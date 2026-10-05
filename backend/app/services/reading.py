"""Reading: ingestion, simplification with coverage, glossing, opt-in, finish (design: M3)."""

import hashlib
import json
import logging
import math
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from wordfreq import zipf_frequency

from app.curriculum.importer import USER_SOURCE
from app.domain.config import ReadingConfig
from app.domain.production import WEAK_MASTERY
from app.domain.words import (
    COVERED,
    STATUS_RANK,
    Classification,
    LearnerView,
    LexiconWord,
    classify,
    coverage,
    pick_implicit,
    sentence_around,
)
from app.llm.calls import last_call_id
from app.llm.client import LLMClient, LLMError, LLMUnavailable
from app.llm.types import Gloss, GlossRequest, OovWord, SimplifiedText, SimplifyRequest
from app.nlp.analyzer import Analyzer, Token
from app.nlp.extract import source_language
from app.services.common import SessionError
from app.services.corpus import item_label
from app.services.learner import projection_config, settings_of
from app.services.sessions import interference_note
from app.store.events import append_event
from app.store.models import (
    Exercise,
    GlossCache,
    Item,
    ItemMemory,
    Learner,
    LearnerItem,
    ReadingSession,
    SourceText,
    TextVersion,
)

logger = logging.getLogger(__name__)

READING_CFG = ReadingConfig()
KNOWN = ("introduced", "presumed_known")
TOKEN_CLASSES_NOT_OPTIN = ("known", "presumed_known")
SUMMARY_TARGETS = 3
REFERENCE_CHARS = 400
SUMMARY_INSTRUCTIONS = {
    "it": "Riassumi o commenta il testo che hai letto in 2-4 frasi in tedesco.",
    "en": "Summarize or comment on the text you read in 2-4 German sentences.",
}


# --- learner view and analysis --------------------------------------------------------------


def load_view(db: Session, learner: Learner) -> tuple[LearnerView, dict[str, Item]]:
    """The learner's lexicon (lemma items, including user-created ones) and the items by id."""
    rows = db.execute(
        select(Item, LearnerItem.status)
        .join(LearnerItem, LearnerItem.item_id == Item.id)
        .where(LearnerItem.learner_id == learner.id, Item.kind == "lemma", ~Item.suspended)
    ).all()
    items = {item.id: item for item, _ in rows}
    words = [
        LexiconWord(
            item.id, item.payload["lemma"], item.cefr_level, status, item.payload.get("pos", "")
        )
        for item, status in rows
        if status in STATUS_RANK
    ]
    return LearnerView.build(words), items


def classify_tokens(tokens: list[Token], view: LearnerView, level: str) -> list[Classification]:
    return [
        classify(
            t.lemma,
            t.pos,
            t.is_propn,
            view,
            level,
            ent_type=t.ent_type,
            surface=t.text,
            is_alpha=t.is_alpha,
        )
        for t in tokens
    ]


def analysis_tokens(tokens: list[Token], classes: list[Classification]) -> list[dict[str, Any]]:
    return [
        {
            "i": t.i,
            "start": t.start,
            "end": t.end,
            "lemma": t.lemma,
            "is_alpha": t.is_alpha,
            "class": c.word_class,
            "item_id": c.item_id,
            "parts": list(c.parts),
        }
        for t, c in zip(tokens, classes, strict=True)
    ]


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def _max_words(source: str, cfg: ReadingConfig) -> int:
    n = _word_count(source)
    if n > cfg.long_text_words:
        return cfg.long_text_max_words
    return max(50, math.ceil(1.2 * n))


def _lemma_of(item: Item) -> str:
    return str(item.payload["lemma"])


# --- simplification loop --------------------------------------------------------------------


def _llm_error(exc: LLMError) -> SessionError:
    if isinstance(exc, LLMUnavailable):
        return SessionError(503, f"The language model is temporarily unavailable: {exc}")
    return SessionError(502, f"The language model failed: {exc}")


def _oov_words(
    tokens: list[Token], classes: list[Classification], candidates: set[str]
) -> list[OovWord]:
    """Counted words outside the learner's vocabulary (candidate words that were offered stay)."""
    found: dict[str, list[str]] = {}
    for token, cls in zip(tokens, classes, strict=True):
        if not token.is_alpha or cls.word_class == "ignore" or cls.word_class in COVERED:
            continue
        if token.lemma in candidates:
            continue
        forms = found.setdefault(token.lemma, [])
        if token.text not in forms:
            forms.append(token.text)
    return [OovWord(lemma=lemma, forms=forms) for lemma, forms in found.items()]


def _simplify(
    learner: Learner,
    base: SimplifyRequest,
    candidates: set[str],
    view: LearnerView,
    llm: LLMClient,
    analyzer: Analyzer,
    cfg: ReadingConfig,
) -> list[dict[str, Any]]:
    """Run the coverage loop; return the attempts (nothing is written to the database)."""
    attempts: list[dict[str, Any]] = []
    previous: str | None = None
    replace: list[OovWord] = []
    error: SessionError | None = None
    for number in range(1, cfg.max_simplify_attempts + 1):
        request = base.model_copy(update={"previous_text": previous, "replace_words": replace})
        try:
            result: SimplifiedText = llm.simplify_text(request)
        except LLMError as exc:
            logger.warning("simplify_text failed (attempt %d): %s", number, exc)
            error = _llm_error(exc)
            break
        call_id = last_call_id()
        paragraphs = [" ".join(p.split()) for p in result.paragraphs if p.strip()]
        if not paragraphs:
            error = SessionError(502, "The language model returned an empty text")
            continue
        body = "\n\n".join(paragraphs)
        tokens = analyzer.analyze(body)
        classes = classify_tokens(tokens, view, learner.level)
        cov = coverage([t.is_alpha for t in tokens], [c.word_class for c in classes])
        attempts.append(
            {
                "attempt": number,
                "title": " ".join(result.title.split()) or "Text",
                "body": body,
                "coverage": cov,
                "llm_call_id": call_id,
                "analysis": {
                    "tokens": analysis_tokens(tokens, classes),
                    "new_words": [w.model_dump() for w in result.new_words],
                    "notes": result.notes,
                },
            }
        )
        if cov >= cfg.coverage_target:
            break
        previous = body
        replace = _oov_words(tokens, classes, candidates)
        if not replace:
            break
    if not attempts:
        raise error or SessionError(502, "The language model returned no text")
    return attempts


def _vocabulary(db: Session, learner: Learner) -> tuple[LearnerView, dict[str, Item], list[str]]:
    view, items = load_view(db, learner)
    known = [
        items[w.item_id] for words in view.by_lemma.values() for w in words if w.status in KNOWN
    ]
    known.sort(key=lambda i: (-(i.frequency_zipf or 0.0), i.id))
    return view, items, list(dict.fromkeys(_lemma_of(i) for i in known))


def _known_grammar(db: Session, learner: Learner) -> list[str]:
    rows = db.execute(
        select(Item)
        .join(LearnerItem, LearnerItem.item_id == Item.id)
        .where(
            LearnerItem.learner_id == learner.id,
            LearnerItem.status.in_(KNOWN),
            Item.kind.in_(("grammar", "construction")),
            ~Item.suspended,
        )
        .order_by(Item.id)
    ).scalars()
    return [i.payload["title_en"] if i.kind == "grammar" else i.payload["pattern"] for i in rows]


def _store(
    db: Session,
    learner: Learner,
    text: SourceText,
    attempts: list[dict[str, Any]],
    now: datetime,
) -> TextVersion:
    best = max(attempts, key=lambda a: (a["coverage"], -a["attempt"]))
    db.add(text)
    db.flush()
    selected: TextVersion | None = None
    for attempt in attempts:
        version = TextVersion(
            text_id=text.id,
            level=learner.level,
            title=attempt["title"],
            body=attempt["body"],
            coverage=attempt["coverage"],
            attempt=attempt["attempt"],
            llm_call_id=attempt["llm_call_id"],
            selected=attempt is best,
            analysis=attempt["analysis"],
            created_at=now,
        )
        db.add(version)
        if attempt is best:
            selected = version
    db.commit()
    assert selected is not None
    return selected


def create_text(
    db: Session,
    learner: Learner,
    *,
    title: str,
    source_text: str,
    source_url: str | None,
    llm: LLMClient,
    analyzer: Analyzer,
    now: datetime,
    cfg: ReadingConfig = READING_CFG,
) -> tuple[SourceText, TextVersion]:
    """Simplify a source text to the learner's level and store the attempts."""
    source_text = source_text.strip()
    if len(source_text) < 40:
        raise SessionError(422, "The text is too short")
    if len(source_text) > cfg.max_source_chars:
        raise SessionError(422, f"The text is longer than {cfg.max_source_chars} characters")
    language = source_language(source_text)
    view, items, allowed = _vocabulary(db, learner)

    candidates: list[str] = []
    if language == "de":
        tokens = analyzer.analyze(source_text)
        classes = classify_tokens(tokens, view, learner.level)
        found = {
            c.item_id: t.lemma
            for t, c in zip(tokens, classes, strict=True)
            if c.word_class == "auto_candidate" and c.item_id
        }
        ranked = sorted(found, key=lambda i: (-(items[i].frequency_zipf or 0.0), i))
        candidates = [_lemma_of(items[i]) for i in ranked[: cfg.max_candidate_words]]

    request = SimplifyRequest(
        mode="simplify",
        source_text=source_text,
        source_language="de" if language == "de" else "other",
        level=learner.level,
        explanation_language=learner.explanation_language,
        allowed_lemmas=allowed,
        candidate_lemmas=candidates,
        known_grammar=_known_grammar(db, learner),
        max_words=_max_words(source_text, cfg),
    )
    attempts = _simplify(learner, request, set(candidates), view, llm, analyzer, cfg)
    row = SourceText(
        learner_id=learner.id,
        source_url=source_url,
        source_title=title.strip() or "Text",
        source_text=source_text,
        source_language=language,
        created_at=now,
    )
    return row, _store(db, learner, row, attempts, now)


def generate_text(
    db: Session,
    learner: Learner,
    topic: str | None,
    llm: LLMClient,
    analyzer: Analyzer,
    now: datetime,
    cfg: ReadingConfig = READING_CFG,
) -> tuple[SourceText, TextVersion]:
    """Write a new text from scratch, seeded with the learner's due lemmas (R§7.3)."""
    view, _items, allowed = _vocabulary(db, learner)
    due = db.execute(
        select(Item, ItemMemory.due)
        .join(ItemMemory, ItemMemory.item_id == Item.id)
        .where(
            ItemMemory.learner_id == learner.id,
            ItemMemory.due.is_not(None),
            ItemMemory.due <= now,
            Item.kind == "lemma",
            ~Item.suspended,
        )
        .order_by(ItemMemory.due, Item.id)
    ).all()
    seeds = list(dict.fromkeys(_lemma_of(item) for item, _ in due))[: cfg.seed_lemmas]
    request = SimplifyRequest(
        mode="generate",
        topic=(topic or "").strip() or None,
        level=learner.level,
        explanation_language=learner.explanation_language,
        allowed_lemmas=allowed,
        seed_lemmas=seeds,
        known_grammar=_known_grammar(db, learner),
        max_words=cfg.generated_words + 50,
    )
    attempts = _simplify(learner, request, set(), view, llm, analyzer, cfg)
    title = request.topic or attempts[0]["title"]
    row = SourceText(
        learner_id=learner.id,
        source_url=None,
        source_title=title,
        source_text="",
        source_language="generated",
        created_at=now,
    )
    return row, _store(db, learner, row, attempts, now)


# --- reading session ------------------------------------------------------------------------


def selected_version(db: Session, text_id: int) -> TextVersion:
    version = db.scalar(
        select(TextVersion).where(TextVersion.text_id == text_id, TextVersion.selected)
    )
    if version is None:
        raise SessionError(404, "Text not found")
    return version


def get_text(db: Session, learner: Learner, text_id: int) -> SourceText:
    text = db.get(SourceText, text_id)
    if text is None or text.learner_id != learner.id:
        raise SessionError(404, "Text not found")
    return text


def start_reading(db: Session, learner: Learner, text_id: int, now: datetime) -> ReadingSession:
    get_text(db, learner, text_id)
    version = selected_version(db, text_id)
    session = ReadingSession(text_version_id=version.id, started_at=now, lookups=[])
    db.add(session)
    db.commit()
    return session


def _open_session(
    db: Session, learner: Learner, reading_id: int
) -> tuple[ReadingSession, TextVersion]:
    reading = db.get(ReadingSession, reading_id)
    version = db.get(TextVersion, reading.text_version_id) if reading else None
    text = db.get(SourceText, version.text_id) if version else None
    if reading is None or version is None or text is None or text.learner_id != learner.id:
        raise SessionError(404, "Reading session not found")
    return reading, version


def _token(version: TextVersion, index: int) -> dict[str, Any]:
    tokens = version.analysis["tokens"]
    if not 0 <= index < len(tokens):
        raise SessionError(404, "Token not found")
    token = tokens[index]
    if not token["is_alpha"]:
        raise SessionError(422, "This token is not a word")
    return token  # type: ignore[no-any-return]


def _user_item_for(db: Session, lemma: str) -> Item | None:
    for item in db.scalars(select(Item).where(Item.source_file == USER_SOURCE, ~Item.suspended)):
        if _lemma_of(item).casefold() == lemma.casefold():
            return item
    return None


def _resolve_item(db: Session, token: dict[str, Any]) -> Item | None:
    """The lexicon item of a token; unlisted tokens may match an item created since the analysis."""
    if token["item_id"]:
        return db.get(Item, token["item_id"])
    return _user_item_for(db, token["lemma"])


def _context_hash(language: str, word: str, sentence: str) -> str:
    blob = json.dumps([language, word, sentence], ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _llm_gloss(
    db: Session,
    learner: Learner,
    version: TextVersion,
    token: dict[str, Any],
    llm: LLMClient,
    now: datetime,
) -> Gloss:
    """The cached gloss of an unlisted word, or a new one from the `gloss` task."""
    word = version.body[token["start"] : token["end"]]
    sentence = sentence_around(version.body, token["start"], token["end"])
    key = _context_hash(learner.explanation_language, word, sentence)
    cached = db.scalar(
        select(GlossCache).where(GlossCache.lemma == token["lemma"], GlossCache.context_hash == key)
    )
    if cached is not None:
        return Gloss.model_validate(cached.data)
    request = GlossRequest(
        word=word,
        lemma=token["lemma"],
        sentence=sentence,
        level=learner.level,
        explanation_language=learner.explanation_language,
    )
    try:
        gloss = llm.gloss(request)
    except LLMError as exc:
        raise _llm_error(exc) from None
    db.add(
        GlossCache(
            lemma=token["lemma"],
            context_hash=key,
            data=gloss.model_dump(mode="json"),
            llm_call_id=last_call_id(),
            created_at=now,
        )
    )
    db.flush()
    return gloss


def _emit(
    db: Session,
    learner: Learner,
    item_id: str,
    *,
    kind: str,
    weight: float,
    now: datetime,
) -> None:
    """A reading event on the recognition facet; a presumed-known item becomes introduced."""
    cfg = projection_config(settings_of(learner))
    learner_item = db.get(LearnerItem, (learner.id, item_id))
    was_presumed = learner_item is not None and learner_item.status == "presumed_known"
    common = {"learner_id": learner.id, "item_id": item_id, "facet": "recognition", "ts": now}
    append_event(
        db,
        cfg,
        kind=kind,
        outcome="assisted" if kind == "lookup" else "correct",
        evidence_weight=weight,
        presumed_known=was_presumed,
        **common,
    )
    if learner_item is not None and learner_item.status != "introduced":
        learner_item.status = "introduced"
        learner_item.introduced_at = learner_item.introduced_at or now
        if was_presumed:
            append_event(db, cfg, kind="status_change", presumed_known=True, **common)


def _can_optin(db: Session, learner: Learner, item: Item | None) -> bool:
    if item is None:
        return True
    row = db.get(LearnerItem, (learner.id, item.id))
    if row is None:
        return True
    return row.status == "unseen" or (row.status == "candidate" and row.candidate_source != "optin")


def gloss_token(
    db: Session,
    learner: Learner,
    reading_id: int,
    token_index: int,
    llm: LLMClient,
    now: datetime,
) -> dict[str, Any]:
    reading, version = _open_session(db, learner, reading_id)
    token = _token(version, token_index)
    item = _resolve_item(db, token)
    if item is not None:
        payload = item.payload
        lang = learner.explanation_language
        result: dict[str, Any] = {
            "translation": payload["translations"].get(lang) or payload["translations"]["it"],
            "lemma": payload["lemma"],
            "pos": payload.get("pos"),
            "gender": payload.get("gender"),
            "plural": payload.get("plural"),
            "note": interference_note(item),
            "source": "lexicon",
            "item_id": item.id,
        }
    else:
        gloss = _llm_gloss(db, learner, version, token, llm, now)
        result = {**gloss.model_dump(mode="json"), "source": "llm", "item_id": None}

    row = db.get(LearnerItem, (learner.id, item.id)) if item is not None else None
    reading.lookups = [
        *reading.lookups,
        {"token_index": token_index, "lemma": token["lemma"], "item_id": item.id if item else None},
    ]
    already = any(lk["item_id"] == item.id for lk in reading.lookups[:-1] if item is not None)
    if row is not None and row.status in KNOWN and not already:
        cfg = projection_config(settings_of(learner))
        _emit(
            db,
            learner,
            row.item_id,
            kind="lookup",
            weight=cfg.weight("lookup"),
            now=now,
        )
    db.commit()
    result["word_class"] = token["class"]
    result["can_optin"] = _can_optin(db, learner, item)
    return result


# --- opt-in ---------------------------------------------------------------------------------

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def user_item_id(db: Session, lemma: str) -> str:
    """`lex:user-<slug>`; a numeric suffix keeps different lemmas with the same slug apart."""
    folded = lemma.casefold().translate(_UMLAUTS)
    ascii_text = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-") or "word"
    candidate, n = f"lex:user-{slug}", 1
    while db.get(Item, candidate) is not None:
        n += 1
        candidate = f"lex:user-{slug}-{n}"
    return candidate


def _create_user_item(db: Session, learner: Learner, gloss: Gloss, fallback_lemma: str) -> Item:
    lemma = gloss.lemma.strip() or fallback_lemma
    translation = gloss.translation.strip() or lemma
    payload: dict[str, Any] = {
        "lemma": lemma,
        "pos": gloss.pos,
        "translations": {"it": translation, "en": translation},
        "user_created": True,
    }
    if gloss.pos == "noun":
        if gloss.gender:
            payload["gender"] = gloss.gender
        if gloss.plural:
            payload["plural"] = gloss.plural
    item = Item(
        id=user_item_id(db, lemma),
        kind="lemma",
        cefr_level=learner.level,
        payload=payload,
        interference={},
        frequency_zipf=float(zipf_frequency(lemma, "de")),
        source_file=USER_SOURCE,
        content_hash=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        suspended=False,
    )
    db.add(item)
    db.flush()
    return item


def optin_token(
    db: Session,
    learner: Learner,
    reading_id: int,
    token_index: int,
    llm: LLMClient,
    now: datetime,
) -> dict[str, Any]:
    """Make the word a candidate with source `optin` (creating a user item when unlisted)."""
    _reading, version = _open_session(db, learner, reading_id)
    token = _token(version, token_index)
    item = _resolve_item(db, token)
    created = False
    if item is None:
        gloss = _llm_gloss(db, learner, version, token, llm, now)
        view, items = load_view(db, learner)
        entry = view.lookup(gloss.lemma) if gloss.lemma.strip() else None
        if entry is not None:
            item = items[entry.item_id]
        else:
            item = _create_user_item(db, learner, gloss, token["lemma"])
            created = True
    row = db.get(LearnerItem, (learner.id, item.id))
    if row is None:
        row = LearnerItem(learner_id=learner.id, item_id=item.id, status="unseen")
        db.add(row)
    if row.status not in KNOWN:
        if row.status != "candidate":
            row.candidate_since = now
        row.status = "candidate"
        row.candidate_source = "optin"
    db.commit()
    return {
        "item_id": item.id,
        "label": item_label(item),
        "status": row.status,
        "created": created,
    }


# --- finish ---------------------------------------------------------------------------------


CONTENT_POS = ("noun", "verb", "adj", "adv")


def _summary_targets(
    db: Session, learner: Learner, item_ids: set[str], now: datetime
) -> list[Item]:
    """Up to three content words of the text that are due or weak (else the weakest ones)."""
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_(item_ids)))}
    content = {i for i, item in items.items() if item.payload.get("pos") in CONTENT_POS}
    pool = content or set(items)
    mastery: dict[str, float] = {}
    due: set[str] = set()
    for row in db.scalars(
        select(ItemMemory).where(ItemMemory.learner_id == learner.id, ItemMemory.item_id.in_(pool))
    ):
        mastery[row.item_id] = min(row.mastery, mastery.get(row.item_id, 1.0))
        if row.due is not None and row.due <= now:
            due.add(row.item_id)
    ranked = sorted(mastery, key=lambda i: (mastery[i], i))
    chosen = [i for i in ranked if i in due or mastery[i] < WEAK_MASTERY] or ranked
    return [items[i] for i in chosen[:SUMMARY_TARGETS]]


def _summary_exercise(
    db: Session,
    learner: Learner,
    reading_id: int,
    version: TextVersion,
    item_ids: set[str],
    now: datetime,
) -> Exercise:
    targets = _summary_targets(db, learner, item_ids, now)
    first = version.body.split("\n\n")[0]
    exercise = Exercise(
        id=uuid.uuid4().hex,
        learner_id=learner.id,
        type="production",
        prompt={
            "subtype": "summary",
            "instructions": SUMMARY_INSTRUCTIONS.get(
                learner.explanation_language, SUMMARY_INSTRUCTIONS["en"]
            ),
            "prompt": version.title,
            "glossary": [
                {
                    "item_id": t.id,
                    "de": item_label(t),
                    "translation": t.payload["translations"].get(learner.explanation_language)
                    or t.payload["translations"]["it"],
                }
                for t in targets
            ],
        },
        solution={
            "reference_solutions": [first[:REFERENCE_CHARS]],
            "reading_session_id": reading_id,
        },
        targets=[
            {
                "item_id": t.id,
                "facet": "production",
                "weight": 1.0 if n == 0 else 0.5,
                "new": False,
                "role": "primary" if n == 0 else "secondary",
                "kind": "lemma",
                "focus_tags": [],
            }
            for n, t in enumerate(targets)
        ],
        generator="reading_summary.v1",
        created_at=now,
        session_id=f"reading-{reading_id}",
        status="ready",
    )
    db.add(exercise)
    return exercise


def finish_reading(
    db: Session,
    learner: Learner,
    reading_id: int,
    now: datetime,
    cfg: ReadingConfig = READING_CFG,
) -> dict[str, Any]:
    reading, version = _open_session(db, learner, reading_id)
    if reading.finished_at is not None:
        raise SessionError(409, "Reading session already finished")
    projection = projection_config(settings_of(learner))
    tokens = version.analysis["tokens"]
    looked_up = {lk["item_id"] for lk in reading.lookups if lk["item_id"]}

    known_ids = {t["item_id"] for t in tokens if t["class"] in TOKEN_CLASSES_NOT_OPTIN}
    known_ids.discard(None)
    status_of = dict(
        db.execute(
            select(LearnerItem.item_id, LearnerItem.status).where(
                LearnerItem.learner_id == learner.id,
                LearnerItem.item_id.in_({t["item_id"] for t in tokens if t["item_id"]}),
            )
        ).all()
    )
    still_known = {i for i in known_ids if status_of.get(i) in KNOWN}
    frequency = {
        i: (db.get(Item, i).frequency_zipf or 0.0)  # type: ignore[union-attr]
        for i in still_known
    }
    implicit = pick_implicit(frequency, looked_up, cfg.max_implicit_per_text)
    for item_id in implicit:
        _emit(
            db,
            learner,
            item_id,
            kind="implicit",
            weight=projection.weight("implicit_reading"),
            now=now,
        )

    candidates: list[str] = []
    for item_id in dict.fromkeys(
        t["item_id"] for t in tokens if t["class"] == "auto_candidate" and t["item_id"]
    ):
        row = db.get(LearnerItem, (learner.id, item_id))
        if row is None:
            continue
        if row.status == "unseen" or (
            row.status == "candidate" and row.candidate_source == "wordlist"
        ):
            if row.status == "unseen":
                row.candidate_since = now
            row.status = "candidate"
            row.candidate_source = "article"
            candidates.append(item_id)

    reading.finished_at = now
    text_items = {t["item_id"] for t in tokens if t["item_id"] and t["class"] in COVERED}
    exercise = _summary_exercise(db, learner, reading_id, version, text_items, now)
    db.commit()
    return {
        "session_id": exercise.session_id,
        "exercise": exercise,
        "implicit_events": len(implicit),
        "candidates": candidates,
    }
