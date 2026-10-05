"""Run the real grading pipeline on the evaluation cases, without a database.

The pipeline is `LLMClient.grade_sentence` followed by `reconcile_answer` (the same function
the app uses); the curriculum comes straight from the YAML files, LLM calls are recorded in
memory (tokens, latency) instead of the `llm_calls` table, and every curriculum item counts as
known to the evaluated learner. LanguageTool is used when reachable; otherwise the matches
recorded in the case file (when present) stand in for it.
"""

import statistics
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from app.curriculum.loader import Curriculum
from app.domain.grader_eval import (
    CaseScore,
    PredictedError,
    Prediction,
    Signature,
    aggregate,
    consistency,
    score_case,
)
from app.domain.reconcile import Evaluation
from app.evals.cases import GraderCase
from app.llm.calls import CallRecord
from app.llm.client import LLMClient, LLMError
from app.llm.types import GradeRequest, TargetContext
from app.nlp.languagetool import LanguageToolClient
from app.nlp.types import LTMatch
from app.services.production import flat_tags, item_context, reconcile_answer
from app.store.models import Item

EXPLANATION_LANGUAGE = "it"


class MemoryRecorder:
    """Collects the records of the LLM calls instead of writing them to the database."""

    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def __call__(self, record: CallRecord) -> int | None:
        self.records.append(record)
        return None


def items_of(curriculum: Curriculum) -> dict[str, Item]:
    """Transient `Item` objects (never attached to a session)."""
    return {
        i.id: Item(
            id=i.id,
            kind=i.kind,
            cefr_level=i.level,
            payload=i.payload,
            interference=i.interference,
            source_file=i.source_file,
            content_hash=i.content_hash,
            suspended=False,
        )
        for i in curriculum.items
    }


def tags_of(items: dict[str, Item]) -> dict[str, set[str]]:
    return {item_id: set(flat_tags(item)) for item_id, item in items.items()}


def grade_request(
    case: GraderCase, items: dict[str, Item], lt_matches: list[LTMatch] | None
) -> GradeRequest:
    ex = case.exercise
    return GradeRequest(
        exercise_type=ex.type,
        instructions=ex.instructions,
        prompt=ex.prompt,
        glossary=ex.glossary,
        reference_solutions=ex.reference_solutions,
        answer=case.answer,
        targets=[
            TargetContext(item=item_context(items[t.item_id]), weight=t.weight, role=t.role)
            for t in ex.targets
        ],
        # As in the app: tags are offered for the exercise targets only.
        allowed_tags={
            t.item_id: flat_tags(items[t.item_id])
            for t in ex.targets
            if flat_tags(items[t.item_id])
        },
        lt_matches=lt_matches,
        level=case.level,
        explanation_language=EXPLANATION_LANGUAGE,
    )


def prediction_of(evaluation: Evaluation) -> Prediction:
    return Prediction(
        overall=evaluation.overall,
        errors=tuple(
            PredictedError(e.item_id, e.start, e.end, tuple(e.diagnostic_tags), e.severity)
            for e in evaluation.errors
        ),
        correct_uses=tuple(i.item_id for i in evaluation.items if i.outcome != "error"),
    )


def _lt_matches(
    case: GraderCase, languagetool: LanguageToolClient | None, live: bool
) -> list[LTMatch] | None:
    if live and languagetool is not None:
        matches = languagetool.check(case.answer) if case.answer.strip() else []
        if matches is not None:
            return matches
    return case.lt_matches


def run_eval(
    cases: Sequence[GraderCase],
    llm: LLMClient,
    recorder: MemoryRecorder,
    items: dict[str, Item],
    *,
    languagetool: LanguageToolClient | None = None,
    repeat: int = 1,
) -> dict[str, Any]:
    """Grade every case `repeat` times and compute the report (a JSON-serializable dict)."""
    live_lt = languagetool is not None and languagetool.check("Test") is not None
    known = set(items)
    case_reports: list[dict[str, Any]] = []
    scores: list[CaseScore] = []
    scores_by_category: dict[str, list[CaseScore]] = defaultdict(list)
    latencies: list[int] = []
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    versions: set[str] = set()
    models: set[str] = set()
    reconcile_versions: set[str] = set()
    failures = 0

    for case in cases:
        request = grade_request(case, items, _lt_matches(case, languagetool, live_lt))
        runs: list[dict[str, Any]] = []
        signatures: list[Signature] = []
        for _ in range(repeat):
            before = len(recorder.records)
            try:
                grade = llm.grade_sentence(request)
                evaluation = reconcile_answer(grade, request, known, known)
            except LLMError as exc:
                failures += 1
                runs.append({"error": str(exc)})
                continue
            record = next(
                (r for r in reversed(recorder.records[before:]) if r.task == "grade_sentence"),
                None,
            )
            prediction = prediction_of(evaluation)
            score = score_case(case.answer, case.expectation(), prediction)
            scores.append(score)
            scores_by_category[case.category].append(score)
            signatures.append(
                Signature(prediction.overall, tuple(e.item_id for e in prediction.errors))
            )
            run: dict[str, Any] = {
                "overall": evaluation.overall,
                "errors": [
                    {
                        "item_id": e.item_id,
                        "original": e.original,
                        "diagnostic_tags": list(e.diagnostic_tags),
                        "severity": e.severity,
                    }
                    for e in evaluation.errors
                ],
                "overall_ok": score.overall_ok,
                "flagged": score.flagged,
                "true_positives": score.true_positives,
            }
            if record is not None:
                run.update(
                    latency_ms=record.latency_ms,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                )
                latencies.append(record.latency_ms)
                tokens["input"] += record.input_tokens or 0
                tokens["output"] += record.output_tokens or 0
                tokens["cache_read"] += record.cache_read_tokens or 0
                tokens["cache_write"] += record.cache_write_tokens or 0
                versions.add(record.prompt_version)
                models.add(record.model)
            reconcile_versions.add(evaluation.reconcile_version)
            runs.append(run)
        agreement = consistency(signatures)
        case_reports.append(
            {
                "id": case.id,
                "category": case.category,
                "expected_overall": case.expected.overall,
                "runs": runs,
                "consistency": (
                    {"overall": agreement[0], "error_items": agreement[1]} if agreement else None
                ),
            }
        )

    consistencies = [c["consistency"] for c in case_reports if c["consistency"]]
    summary = aggregate(scores).to_dict()
    summary["failed_runs"] = failures
    summary["consistency_overall"] = (
        statistics.fmean(c["overall"] for c in consistencies) if consistencies else None
    )
    summary["consistency_error_items"] = (
        statistics.fmean(c["error_items"] for c in consistencies) if consistencies else None
    )
    # The provider/model that graded (`--provider` / `--model` overrides land here).
    grader = getattr(llm, "routes", {}).get("grade_sentence")
    return {
        "config": {
            "cases": len(cases),
            "repeat": repeat,
            "llm": grader.partition("/")[0] if grader else llm.name,
            "grader": grader,
            "models": sorted(models),
            "prompt_versions": sorted(versions),
            "reconcile_versions": sorted(reconcile_versions),
            "languagetool": "live" if live_lt else "recorded",
        },
        "summary": summary,
        "by_category": {
            category: aggregate(group).to_dict()
            for category, group in sorted(scores_by_category.items())
        },
        "cost": {
            "tokens": tokens,
            "calls": len(latencies),
            "latency_ms_mean": statistics.fmean(latencies) if latencies else None,
            "latency_ms_max": max(latencies) if latencies else None,
        },
        "cases": case_reports,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def format_summary(report: dict[str, Any]) -> str:
    cfg, s, cost = report["config"], report["summary"], report["cost"]
    lines = [
        f"grader evaluation: {cfg['cases']} cases x {cfg['repeat']} run(s), llm={cfg['llm']} "
        f"models={','.join(cfg['models']) or '-'} prompts={','.join(cfg['prompt_versions']) or '-'}"
        f" languagetool={cfg['languagetool']}",
        "",
        f"  overall accuracy       {_pct(s['overall_accuracy'])}",
        f"  error precision        {_pct(s['precision'])}",
        f"  error recall           {_pct(s['recall'])}",
        f"  error F1               {_pct(s['f1'])}",
        f"  false-positive rate    {_pct(s['false_positive_rate'])}"
        f"  (on {s['n_correct_cases']} correct answers)",
        f"  attribution accuracy   {_pct(s['attribution_accuracy'])}",
        f"  tag accuracy           {_pct(s['tag_accuracy'])}",
        f"  correct-use recall     {_pct(s['correct_use_recall'])}",
        f"  consistency (overall)  {_pct(s['consistency_overall'])}",
        f"  failed runs            {s['failed_runs']}",
        "",
        f"  tokens in/out          {cost['tokens']['input']}/{cost['tokens']['output']}"
        f"  (cache read {cost['tokens']['cache_read']})",
        f"  latency mean/max       "
        f"{'n/a' if cost['latency_ms_mean'] is None else round(cost['latency_ms_mean'])}"
        f"/{cost['latency_ms_max']} ms",
        "",
        f"  {'category':<18}{'n':>4}{'overall acc':>14}{'FP rate':>10}{'F1':>8}",
    ]
    for category, m in report["by_category"].items():
        lines.append(
            f"  {category:<18}{m['n']:>4}{_pct(m['overall_accuracy']):>14}"
            f"{_pct(m['false_positive_rate']):>10}{_pct(m['f1']):>8}"
        )
    return "\n".join(lines)
