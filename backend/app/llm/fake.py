"""Deterministic `LLMClient` without network access (tests, and runs without an API key)."""

import json
import re
import time

from app.llm.calls import CallRecord, CallRecorder, announce_start
from app.llm.client import LLMError
from app.llm.config import DEFAULT_TASKS
from app.llm.types import (
    CLOSED_SUBTYPES,
    GAP,
    ExerciseRequest,
    ExplainRequest,
    Explanation,
    ExplanationExample,
    GeneratedExercise,
    Gloss,
    GlossEntry,
    GlossRequest,
    GradeError,
    GradeRequest,
    GradeResult,
    SimplifiedText,
    SimplifyRequest,
    TargetWeight,
)

FAKE_MODEL = "fake"
FAKE_VERSION = "fake-v1"

INSTRUCTIONS_IT = {
    "translation": "Traduci in tedesco.",
    "guided": "Scrivi una frase in tedesco per dire quanto segue.",
    "transform": "Scrivi in tedesco la frase seguente.",
    "cloze": "Completa la frase.",
    "choice": "Scegli la forma che completa la frase.",
}
DISTRACTORS = ("der", "die", "das", "den", "dem", "des")


def normalize(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text.strip()).casefold()
    return collapsed.rstrip(".!? ")


def first_paragraph(markdown: str) -> str:
    """First paragraph of a Markdown text, skipping heading lines."""
    for paragraph in re.split(r"\n\s*\n", markdown.strip()):
        lines = [line for line in paragraph.strip().splitlines() if not line.startswith("#")]
        if lines:
            return "\n".join(lines).strip()
    return markdown.strip()


class FakeLLMClient:
    name = "fake"
    routes = {task: f"fake/{FAKE_MODEL}" for task in DEFAULT_TASKS}

    def __init__(self, recorder: CallRecorder | None = None) -> None:
        self._record = recorder

    def _log(self, task: str, request: object, response: object, started: float) -> None:
        if self._record is None:
            return
        payload = request.model_dump(mode="json")  # type: ignore[attr-defined]
        record = CallRecord(
            task=task,
            prompt_version=FAKE_VERSION,
            provider="fake",
            model=FAKE_MODEL,
            request=payload,
            response=response.model_dump(mode="json"),  # type: ignore[attr-defined]
            stop_reason="end_turn",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_ms=int((time.monotonic() - started) * 1000),
            request_chars=len(json.dumps(payload, ensure_ascii=False)),
        )
        # The fake client answers instantly: announce the start right before the result so the
        # debug pane can be tried without an API key.
        announce_start(self._record, record)
        self._record(record)

    def generate_exercise(self, req: ExerciseRequest) -> GeneratedExercise:
        started = time.monotonic()
        if not req.targets:
            raise LLMError("no targets")
        source = next((t.item.example for t in req.targets if t.item.example), None)
        glossary = [
            GlossEntry(
                item_id=t.item.item_id,
                de=t.item.label,
                translation=t.item.translation_it or t.item.label,
            )
            for t in req.targets
            if t.item.kind == "lemma" and t.is_new
        ]
        options: list[str] = []
        if req.exercise_type in CLOSED_SUBTYPES:
            # Gap the first word of the example sentence (or of the label).
            sentence = source.de if source is not None else f"{req.targets[0].item.label} ."
            word, rest = sentence.split(" ", 1)
            prompt, solutions = f"{GAP} {rest}", [word]
            if req.exercise_type == "choice":
                others = [d for d in DISTRACTORS if d.casefold() != word.casefold()]
                options = [word, *others[:3]]
        elif source is not None:
            prompt, solutions = source.it, [source.de]
        else:
            prompt, solutions = req.targets[0].item.label, [req.targets[0].item.label]
        result = GeneratedExercise(
            instructions=INSTRUCTIONS_IT[req.exercise_type],
            prompt=prompt,
            glossary=glossary,
            reference_solutions=solutions,
            targets=[TargetWeight(item_id=t.item.item_id, weight=t.weight) for t in req.targets],
            options=options,
        )
        self._log("generate_exercise", req, result, started)
        return result

    def grade_sentence(self, req: GradeRequest) -> GradeResult:
        started = time.monotonic()
        answer = req.answer
        target_ids = [t.item.item_id for t in req.targets]
        references = {normalize(s) for s in req.reference_solutions}
        if answer.strip() and req.exercise_type == "summary":
            result = GradeResult(
                overall="correct",
                correct_uses=target_ids,
                corrected_sentence=answer,
                feedback="Ottimo riassunto!",
            )
        elif not answer.strip():
            result = GradeResult(
                overall="off_task",
                corrected_sentence=req.reference_solutions[0],
                feedback="Non hai scritto nessuna risposta.",
            )
        elif normalize(answer) in references:
            result = GradeResult(
                overall="correct",
                correct_uses=target_ids,
                corrected_sentence=answer,
                feedback="Corretto! Ottimo lavoro.",
            )
        else:
            expected = req.reference_solutions[0]
            result = GradeResult(
                overall="major_errors",
                errors=[
                    GradeError(
                        start=0,
                        end=len(answer),
                        original=answer,
                        correction=expected,
                        item_id=target_ids[0] if target_ids else None,
                        diagnostic_tags=[],
                        severity="major",
                        confidence=0.9,
                        explanation=f"La risposta attesa era: {expected}",
                    )
                ],
                corrected_sentence=expected,
                feedback=f"Non ci siamo ancora. Una risposta corretta è: {expected}",
            )
        self._log("grade_sentence", req, result, started)
        return result

    def explain(self, req: ExplainRequest) -> Explanation:
        started = time.monotonic()
        text = first_paragraph(req.item.reference_it or req.item.label)
        examples = (
            [ExplanationExample(de=req.item.example.de, translation=req.item.example.it)]
            if req.item.example
            else []
        )
        result = Explanation(markdown=text, examples=examples)
        self._log("explain", req, result, started)
        return result

    def simplify_text(self, req: SimplifyRequest) -> SimplifiedText:
        """Source split into paragraphs; the words it is told to replace become a known word."""
        started = time.monotonic()
        if req.mode == "generate":
            words = req.seed_lemmas or req.allowed_lemmas[:5]
            body = "Heute sprechen wir über: " + ", ".join(words) + "."
            title = req.topic or "Ein neuer Text"
        else:
            body = req.previous_text or req.source_text
            title = " ".join(req.source_text.split()[:6]) or "Text"
        replacement = req.allowed_lemmas[0] if req.allowed_lemmas else "und"
        for word in req.replace_words:
            for form in {word.lemma, *word.forms}:
                body = re.sub(rf"\b{re.escape(form)}\b", replacement, body, flags=re.IGNORECASE)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        result = SimplifiedText(title=title, paragraphs=paragraphs, new_words=[], notes="")
        self._log("simplify_text", req, result, started)
        return result

    def gloss(self, req: GlossRequest) -> Gloss:
        started = time.monotonic()
        noun = req.lemma[:1].isupper()
        result = Gloss(
            translation=f"{req.lemma.lower()} (it)",
            lemma=req.lemma,
            pos="noun" if noun else "adj",
            gender="n" if noun else None,
            plural=f"{req.lemma}e" if noun else None,
            note="fake gloss",
        )
        self._log("gloss", req, result, started)
        return result
