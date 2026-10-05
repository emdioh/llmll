"""Mastery: an evidence-weighted exponential moving average."""

from app.domain.config import ProjectionConfig

OUTCOME_SCORE = {"correct": 1.0, "assisted": 0.5, "error": 0.0}
INITIAL_MASTERY = 0.5
INITIAL_N_EFF = 0.0
TAG_DROP_THRESHOLD = 0.05


def effective_weight(weight: float, confidence: float, cfg: ProjectionConfig) -> float:
    """Uncertain events count with reduced weight."""
    return weight * confidence if confidence < cfg.uncertain_confidence else weight


def update_mastery(
    mastery: float,
    n_eff: float,
    outcome: str,
    weight: float,
    confidence: float,
    cfg: ProjectionConfig,
) -> tuple[float, float]:
    w = effective_weight(weight, confidence, cfg)
    step = min(cfg.alpha * w, 1.0)
    new_mastery = mastery + step * (OUTCOME_SCORE[outcome] - mastery)
    return new_mastery, cfg.decay * n_eff + w


def update_tag_counts(
    counts: dict[str, float], outcome: str, tags: tuple[str, ...], cfg: ProjectionConfig
) -> dict[str, float]:
    """Decay all counts, then add one per tag on an error; drop negligible counts."""
    result = {tag: count * cfg.decay for tag, count in counts.items()}
    if outcome == "error":
        for tag in tags:
            result[tag] = result.get(tag, 0.0) + 1.0
    return {tag: count for tag, count in result.items() if count >= TAG_DROP_THRESHOLD}
