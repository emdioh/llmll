"""Request and response models of the LLM tasks (design: docs/design/M2.md §2.2, §3.2, §4).

Response models are the structured-output schemas: no unions of objects, no free-form dicts,
every field required or defaulted.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.nlp.types import LTMatch

ExerciseSubtype = Literal["translation", "guided", "transform", "summary"]
Overall = Literal["correct", "minor_errors", "major_errors", "off_task"]
Severity = Literal["minor", "major"]


class ExamplePair(BaseModel):
    de: str
    it: str


class ItemContext(BaseModel):
    """Curriculum data of one item, as shown to the model."""

    item_id: str
    kind: Literal["lemma", "grammar", "construction"]
    label: str
    level: str
    translation_it: str | None = None
    translation_en: str | None = None
    pos: str | None = None
    gender: str | None = None
    plural: str | None = None
    pattern: str | None = None
    example: ExamplePair | None = None
    reference_it: str | None = None
    diagnostic_tags: dict[str, list[str]] = Field(default_factory=dict)


class TargetContext(BaseModel):
    item: ItemContext
    is_new: bool = False
    weight: float = 1.0
    role: Literal["primary", "secondary"] = "primary"
    focus_tags: list[str] = Field(default_factory=list)


class VocabEntry(BaseModel):
    item_id: str
    de: str
    translation: str


# --- generate_exercise ---------------------------------------------------------------------


class ExerciseRequest(BaseModel):
    exercise_type: ExerciseSubtype
    level: str
    explanation_language: str
    targets: list[TargetContext]
    known_vocabulary: list[VocabEntry] = Field(default_factory=list)


class GlossEntry(BaseModel):
    item_id: str
    de: str
    translation: str


class TargetWeight(BaseModel):
    item_id: str
    weight: float


class GeneratedExercise(BaseModel):
    instructions: str
    prompt: str
    glossary: list[GlossEntry]
    reference_solutions: list[str]
    targets: list[TargetWeight]


# --- grade_sentence ------------------------------------------------------------------------


class GradeRequest(BaseModel):
    exercise_type: ExerciseSubtype
    instructions: str
    prompt: str
    glossary: list[GlossEntry]
    reference_solutions: list[str]
    answer: str
    targets: list[TargetContext]
    allowed_tags: dict[str, list[str]] = Field(default_factory=dict)
    lt_matches: list[LTMatch] | None = None
    level: str
    explanation_language: str


class GradeError(BaseModel):
    start: int
    end: int
    original: str
    correction: str
    item_id: str | None = None
    diagnostic_tags: list[str] = Field(default_factory=list)
    severity: Severity
    confidence: float
    explanation: str


class GradeResult(BaseModel):
    overall: Overall
    errors: list[GradeError] = Field(default_factory=list)
    correct_uses: list[str] = Field(default_factory=list)
    corrected_sentence: str
    feedback: str


# --- explain -------------------------------------------------------------------------------


class ErrorContext(BaseModel):
    answer: str
    original: str
    correction: str
    diagnostic_tags: list[str] = Field(default_factory=list)


class KnownGrammar(BaseModel):
    item_id: str
    title: str


class ExplainRequest(BaseModel):
    item: ItemContext
    error: ErrorContext | None = None
    question: str | None = None
    level: str
    explanation_language: str
    known_grammar: list[KnownGrammar] = Field(default_factory=list)


class ExplanationExample(BaseModel):
    de: str
    translation: str


class Explanation(BaseModel):
    markdown: str
    examples: list[ExplanationExample] = Field(default_factory=list)


# --- simplify_text -------------------------------------------------------------------------


class OovWord(BaseModel):
    """A lemma of the previous attempt that is outside the learner's vocabulary."""

    lemma: str
    forms: list[str] = Field(default_factory=list)


class SimplifyRequest(BaseModel):
    mode: Literal["simplify", "generate"] = "simplify"
    source_text: str = ""
    source_language: Literal["de", "other"] = "de"
    topic: str | None = None
    level: str
    explanation_language: str
    allowed_lemmas: list[str] = Field(default_factory=list)
    candidate_lemmas: list[str] = Field(default_factory=list)
    seed_lemmas: list[str] = Field(default_factory=list)
    known_grammar: list[str] = Field(default_factory=list)
    max_words: int = 400
    previous_text: str | None = None
    replace_words: list[OovWord] = Field(default_factory=list)


class NewWord(BaseModel):
    lemma: str
    translation: str


class SimplifiedText(BaseModel):
    title: str
    paragraphs: list[str]
    new_words: list[NewWord] = Field(default_factory=list)
    notes: str = ""


# --- gloss ---------------------------------------------------------------------------------

GlossPos = Literal[
    "noun", "verb", "adj", "adv", "prep", "conj", "pron", "det", "num", "particle", "phrase"
]


class GlossRequest(BaseModel):
    word: str
    lemma: str
    sentence: str
    level: str
    explanation_language: str


class Gloss(BaseModel):
    translation: str
    lemma: str
    pos: GlossPos = "noun"
    gender: Literal["m", "f", "n"] | None = None
    plural: str | None = None
    note: str | None = None
