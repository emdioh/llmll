"""From outcome to FSRS rating (R§4.3)."""

from dataclasses import dataclass

from fsrs import Rating

from app.domain.config import ProjectionConfig


@dataclass(frozen=True)
class GradeDecision:
    rating: Rating | None
    needs_remediation: bool = False


def grade(
    outcome: str,
    mastery: float,
    n_eff: float,
    confidence: float,
    presumed_known: bool,
    is_first_review: bool,
    cfg: ProjectionConfig,
) -> GradeDecision:
    """`mastery` and `n_eff` are the values before the event."""
    if confidence < cfg.uncertain_confidence:
        return GradeDecision(rating=None)
    if outcome == "correct":
        easy = presumed_known and is_first_review
        return GradeDecision(rating=Rating.Easy if easy else Rating.Good)
    if outcome == "assisted":
        return GradeDecision(rating=Rating.Hard)
    if mastery >= cfg.slip_mastery_threshold and n_eff >= cfg.slip_min_n_eff:
        return GradeDecision(rating=Rating.Hard)
    return GradeDecision(rating=Rating.Again, needs_remediation=True)
