"""Shared builders for the LLM tests."""

from app.llm.types import (
    ExamplePair,
    ExerciseRequest,
    ExplainRequest,
    GradeRequest,
    ItemContext,
    TargetContext,
    VocabEntry,
)

GRAMMAR = ItemContext(
    item_id="gram:cases",
    kind="grammar",
    label="Casi",
    level="A2",
    translation_it="Casi",
    translation_en="Cases",
    reference_it="## Regola\n\nIn tedesco: *der*, *die*, *das*.",
    diagnostic_tags={"gender": ["m", "f", "n"]},
    example=ExamplePair(de="Der Tisch.", it="Il tavolo."),
)
LEMMA = ItemContext(
    item_id="lex:fenster",
    kind="lemma",
    label="das Fenster",
    level="A2",
    translation_it="finestra",
    translation_en="window",
    pos="noun",
    gender="n",
    plural="Fenster",
    example=ExamplePair(de="Das Fenster ist offen.", it="La finestra è aperta."),
)


def targets() -> list[TargetContext]:
    return [
        TargetContext(item=GRAMMAR, is_new=True, weight=1.0, role="primary"),
        TargetContext(item=LEMMA, is_new=True, weight=0.3, role="secondary"),
    ]


def exercise_request() -> ExerciseRequest:
    return ExerciseRequest(
        exercise_type="translation",
        level="A2",
        explanation_language="it",
        targets=targets(),
        known_vocabulary=[VocabEntry(item_id="lex:tisch", de="der Tisch", translation="tavolo")],
    )


def grade_request(answer: str = "Der Tisch.") -> GradeRequest:
    return GradeRequest(
        exercise_type="translation",
        instructions="Traduci in tedesco.",
        prompt="Il tavolo.",
        glossary=[],
        reference_solutions=["Der Tisch."],
        answer=answer,
        targets=targets(),
        allowed_tags={"gram:cases": ["m", "f", "n"]},
        lt_matches=None,
        level="A2",
        explanation_language="it",
    )


def explain_request() -> ExplainRequest:
    return ExplainRequest(
        item=GRAMMAR, question="Perché?", level="A2", explanation_language="it", known_grammar=[]
    )
