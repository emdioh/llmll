"""Deterministic `LLMClient` without network access (tests, and runs without an API key)."""

import re
import time

from app.llm.calls import CallRecord, CallRecorder
from app.llm.client import LLMError
from app.llm.types import (
    ExerciseRequest,
    ExplainRequest,
    Explanation,
    ExplanationExample,
    GeneratedExercise,
    GlossEntry,
    GradeError,
    GradeRequest,
    GradeResult,
    TargetWeight,
)

FAKE_MODEL = "fake"
FAKE_VERSION = "fake-v1"

INSTRUCTIONS_IT = {
    "translation": "Traduci in tedesco.",
    "guided": "Scrivi una frase in tedesco per dire quanto segue.",
    "transform": "Scrivi in tedesco la frase seguente.",
}


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

    def __init__(self, recorder: CallRecorder | None = None) -> None:
        self._record = recorder

    def _log(self, task: str, request: object, response: object, started: float) -> None:
        if self._record is None:
            return
        self._record(
            CallRecord(
                task=task,
                prompt_version=FAKE_VERSION,
                model=FAKE_MODEL,
                request=request.model_dump(mode="json"),  # type: ignore[attr-defined]
                response=response.model_dump(mode="json"),  # type: ignore[attr-defined]
                stop_reason="end_turn",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        )

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
        if source is not None:
            prompt, solutions = source.it, [source.de]
        else:
            prompt, solutions = req.targets[0].item.label, [req.targets[0].item.label]
        result = GeneratedExercise(
            instructions=INSTRUCTIONS_IT[req.exercise_type],
            prompt=prompt,
            glossary=glossary,
            reference_solutions=solutions,
            targets=[TargetWeight(item_id=t.item.item_id, weight=t.weight) for t in req.targets],
        )
        self._log("generate_exercise", req, result, started)
        return result

    def grade_sentence(self, req: GradeRequest) -> GradeResult:
        started = time.monotonic()
        answer = req.answer
        target_ids = [t.item.item_id for t in req.targets]
        references = {normalize(s) for s in req.reference_solutions}
        if not answer.strip():
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
