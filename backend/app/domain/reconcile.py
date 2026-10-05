"""Reconciliation of the LLM grade with LanguageTool (design: docs/design/M2.md §3.3). Pure."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from app.domain.config import ReconcileConfig
from app.llm.types import GradeError, GradeResult
from app.nlp.types import LTMatch


@dataclass(frozen=True)
class ItemOutcome:
    item_id: str
    outcome: str  # correct | assisted | error
    confidence: float
    diagnostic_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Evaluation:
    overall: str
    items: tuple[ItemOutcome, ...]
    errors: tuple[GradeError, ...]
    unmatched_lt: tuple[LTMatch, ...]
    notes: tuple[str, ...]
    lt_available: bool
    reconcile_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "items": [
                {**asdict(i), "diagnostic_tags": list(i.diagnostic_tags)} for i in self.items
            ],
            "errors": [e.model_dump(mode="json") for e in self.errors],
            "unmatched_lt": [m.model_dump(mode="json") for m in self.unmatched_lt],
            "notes": list(self.notes),
            "lt_available": self.lt_available,
            "reconcile_version": self.reconcile_version,
        }


def _overlaps(start: int, end: int, match: LTMatch) -> bool:
    a0, a1 = start, max(end, start + 1)
    b0, b1 = match.offset, match.offset + max(match.length, 1)
    return a0 < b1 and b0 < a1


def repair_span(error: GradeError, answer: str) -> tuple[GradeError, str | None]:
    """Make `start`/`end` agree with `original` in the answer (models are poor at offsets)."""
    n = len(answer)
    start, end = error.start, error.end
    if 0 <= start <= end <= n and answer[start:end] == error.original:
        return error, None
    if error.original:
        positions = []
        pos = answer.find(error.original)
        while pos != -1:
            positions.append(pos)
            pos = answer.find(error.original, pos + 1)
        if positions:
            best = min(positions, key=lambda p: abs(p - start))
            return error.model_copy(update={"start": best, "end": best + len(error.original)}), (
                f"span of {error.original!r} repaired"
            )
    start = min(max(start, 0), n)
    end = min(max(end, start), n)
    return error.model_copy(update={"start": start, "end": end}), (
        f"span of {error.original!r} clamped"
    )


def reconcile(
    grade: GradeResult,
    lt_matches: Sequence[LTMatch] | None,
    targets: Sequence[str],
    known_item_ids: Collection[str],
    allowed_tags: Mapping[str, Collection[str]],
    cfg: ReconcileConfig,
    *,
    curriculum_item_ids: Collection[str],
    answer: str = "",
    used_hint: bool = False,
) -> Evaluation:
    """Combine the LLM grade and LanguageTool matches into per-item outcomes.

    `targets` are the exercise's target item ids; `known_item_ids` the items the learner knows
    (introduced or presumed known); `curriculum_item_ids` every valid item id.
    """
    notes: list[str] = []
    cleaned: list[GradeError] = []
    for raw in grade.errors:
        error = raw.model_copy(update={"confidence": min(max(raw.confidence, 0.0), 1.0)})
        if answer:
            error, note = repair_span(error, answer)
            if note:
                notes.append(note)
        item_id = error.item_id
        if item_id is not None and item_id not in curriculum_item_ids:
            notes.append(f"dropped unknown item id {item_id!r}")
            item_id = None
        allowed = set(allowed_tags.get(item_id, ())) if item_id is not None else set()
        tags = [t for t in error.diagnostic_tags if t in allowed]
        dropped = [t for t in error.diagnostic_tags if t not in allowed]
        if dropped:
            notes.append(f"dropped tags {dropped} not allowed for {item_id!r}")
        cleaned.append(error.model_copy(update={"item_id": item_id, "diagnostic_tags": tags}))

    lt_available = lt_matches is not None
    matches = list(lt_matches or [])
    adjusted: list[GradeError] = []
    for error in cleaned:
        confidence = error.confidence
        if lt_available:
            if any(_overlaps(error.start, error.end, m) for m in matches):
                confidence = max(confidence, cfg.lt_agree_confidence)
            elif error.severity == "major" and confidence < cfg.lt_disagree_below:
                confidence *= cfg.lt_disagree_factor
        adjusted.append(error.model_copy(update={"confidence": confidence}))
    unmatched = (
        [m for m in matches if not any(_overlaps(e.start, e.end, m) for e in adjusted)]
        if lt_available
        else []
    )
    correct_confidence = cfg.unmatched_lt_confidence if unmatched else 1.0

    correct_uses = set(grade.correct_uses)
    target_set = set(targets)
    known = set(known_item_ids)
    candidates = list(
        dict.fromkeys([*targets, *[e.item_id for e in adjusted if e.item_id], *grade.correct_uses])
    )
    items: list[ItemOutcome] = []
    for item_id in candidates:
        is_target = item_id in target_set
        if not is_target and (item_id not in known or item_id not in curriculum_item_ids):
            continue
        errors = [e for e in adjusted if e.item_id == item_id]
        tags = tuple(dict.fromkeys(t for e in errors for t in e.diagnostic_tags))
        if grade.overall == "off_task" and is_target:
            items.append(ItemOutcome(item_id, "error", cfg.off_task_confidence, tags))
            continue
        if errors:
            outcome = "error" if any(e.severity == "major" for e in errors) else "assisted"
            items.append(ItemOutcome(item_id, outcome, min(e.confidence for e in errors), tags))
            continue
        if item_id in correct_uses or (is_target and grade.overall in ("correct", "minor_errors")):
            outcome = "assisted" if used_hint else "correct"
            items.append(ItemOutcome(item_id, outcome, correct_confidence))
    return Evaluation(
        overall=grade.overall,
        items=tuple(items),
        errors=tuple(adjusted),
        unmatched_lt=tuple(unmatched),
        notes=tuple(notes),
        lt_available=lt_available,
        reconcile_version=cfg.version,
    )
