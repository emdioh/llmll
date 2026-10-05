"""Metrics of the grader evaluation (design: docs/design/M5.md §1-2). Pure.

Definitions (all counts are micro-averaged over cases and runs):
- An expected error is *detected* when a predicted error has the same `item_id` (null equals
  null) and its span overlaps the expected `span_text` (located in the answer by text; an empty
  `span_text` matches any span). Precision/recall/F1 use these strict matches.
- *Attribution accuracy*: of the errors whose span matches (regardless of item id), the share
  whose item id matches too.
- *Tag accuracy*: of the strictly detected errors with expected tags, the share whose predicted
  tags contain all the expected ones.
- *False-positive rate*: of the cases whose expected overall is `correct`, the share where the
  prediction is not `correct` or contains at least one error.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

OVERALL_LABELS = ("correct", "minor_errors", "major_errors", "off_task")


@dataclass(frozen=True)
class ExpectedError:
    item_id: str | None
    span_text: str
    diagnostic_tags: tuple[str, ...] = ()
    severity: str = "major"


@dataclass(frozen=True)
class Expectation:
    overall: str
    errors: tuple[ExpectedError, ...] = ()
    correct_uses: tuple[str, ...] = ()


@dataclass(frozen=True)
class PredictedError:
    item_id: str | None
    start: int
    end: int
    diagnostic_tags: tuple[str, ...] = ()
    severity: str = "major"


@dataclass(frozen=True)
class Prediction:
    overall: str
    errors: tuple[PredictedError, ...] = ()
    correct_uses: tuple[str, ...] = ()  # items judged correct or assisted


@dataclass(frozen=True)
class CaseScore:
    overall_ok: bool
    expected_errors: int
    predicted_errors: int
    true_positives: int
    span_matches: int  # includes the strict ones
    tag_total: int
    tag_ok: int
    correct_uses_expected: int
    correct_uses_found: int
    expects_correct: bool
    flagged: bool  # prediction is not clean although the answer is expected to be correct


def span_overlaps(answer: str, span_text: str, start: int, end: int) -> bool:
    """Does the predicted `[start, end)` overlap the (first) occurrence of `span_text`?"""
    if not span_text:
        return True
    pos = answer.find(span_text)
    if pos == -1:
        pos = answer.casefold().find(span_text.casefold())
    if pos == -1:
        return False
    a0, a1 = pos, pos + len(span_text)
    b0, b1 = start, max(end, start + 1)
    return a0 < b1 and b0 < a1


def match_errors(
    answer: str, expected: Sequence[ExpectedError], predicted: Sequence[PredictedError]
) -> list[tuple[int, int, bool]]:
    """Greedy one-to-one pairs `(expected index, predicted index, item ids equal)`.

    Pairs with equal item ids are formed first; leftovers are paired on span overlap alone.
    """
    pairs: list[tuple[int, int, bool]] = []
    used_e: set[int] = set()
    used_p: set[int] = set()
    for strict in (True, False):
        for i, e in enumerate(expected):
            if i in used_e:
                continue
            for j, p in enumerate(predicted):
                if j in used_p or (strict and e.item_id != p.item_id):
                    continue
                if span_overlaps(answer, e.span_text, p.start, p.end):
                    pairs.append((i, j, e.item_id == p.item_id))
                    used_e.add(i)
                    used_p.add(j)
                    break
    return pairs


def score_case(answer: str, expected: Expectation, predicted: Prediction) -> CaseScore:
    pairs = match_errors(answer, expected.errors, predicted.errors)
    strict = [(i, j) for i, j, same in pairs if same]
    tag_pairs = [(i, j) for i, j in strict if expected.errors[i].diagnostic_tags]
    tag_ok = sum(
        1
        for i, j in tag_pairs
        if set(expected.errors[i].diagnostic_tags) <= set(predicted.errors[j].diagnostic_tags)
    )
    found = set(predicted.correct_uses)
    expects_correct = expected.overall == "correct"
    return CaseScore(
        overall_ok=predicted.overall == expected.overall,
        expected_errors=len(expected.errors),
        predicted_errors=len(predicted.errors),
        true_positives=len(strict),
        span_matches=len(pairs),
        tag_total=len(tag_pairs),
        tag_ok=tag_ok,
        correct_uses_expected=len(expected.correct_uses),
        correct_uses_found=sum(1 for u in expected.correct_uses if u in found),
        expects_correct=expects_correct,
        flagged=expects_correct and (predicted.overall != "correct" or bool(predicted.errors)),
    )


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


@dataclass(frozen=True)
class Metrics:
    n: int
    overall_accuracy: float | None
    precision: float | None
    recall: float | None
    f1: float | None
    false_positive_rate: float | None
    n_correct_cases: int
    attribution_accuracy: float | None
    tag_accuracy: float | None
    correct_use_recall: float | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def aggregate(scores: Sequence[CaseScore]) -> Metrics:
    tp = sum(s.true_positives for s in scores)
    predicted = sum(s.predicted_errors for s in scores)
    expected = sum(s.expected_errors for s in scores)
    precision = _ratio(tp, predicted)
    recall = _ratio(tp, expected)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall > 0
        else (0.0 if precision is not None and recall is not None else None)
    )
    correct_cases = [s for s in scores if s.expects_correct]
    return Metrics(
        n=len(scores),
        overall_accuracy=_ratio(sum(s.overall_ok for s in scores), len(scores)),
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_rate=_ratio(sum(s.flagged for s in correct_cases), len(correct_cases)),
        n_correct_cases=len(correct_cases),
        attribution_accuracy=_ratio(tp, sum(s.span_matches for s in scores)),
        tag_accuracy=_ratio(sum(s.tag_ok for s in scores), sum(s.tag_total for s in scores)),
        correct_use_recall=_ratio(
            sum(s.correct_uses_found for s in scores),
            sum(s.correct_uses_expected for s in scores),
        ),
    )


@dataclass(frozen=True)
class Signature:
    """What a run predicted, reduced to what consistency compares."""

    overall: str
    error_items: tuple[str | None, ...] = field(default=())


def consistency(runs: Sequence[Signature]) -> tuple[float, float] | None:
    """`(overall agreement, error-item agreement)` across repeated runs of one case.

    Each is the share of run pairs that agree; `None` with fewer than two runs.
    """
    if len(runs) < 2:
        return None
    n = len(runs)
    pairs = n * (n - 1) / 2
    overall = Counter(r.overall for r in runs)
    items = Counter(tuple(sorted(map(str, r.error_items))) for r in runs)

    def agree(counter: Counter[Any]) -> float:
        return sum(c * (c - 1) / 2 for c in counter.values()) / pairs

    return agree(overall), agree(items)
