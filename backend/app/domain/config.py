"""Projection and learner configuration (frozen, hashable-by-value)."""

import hashlib
import json
from dataclasses import asdict, dataclass, field


def _default_weights() -> tuple[tuple[str, float], ...]:
    return (
        ("flashcard_recognition", 0.5),
        ("flashcard_production", 0.8),
        ("guided", 1.0),
        ("translation", 1.0),
        ("free", 1.2),
        ("implicit_reading", 0.2),
        ("implicit_sentence", 0.6),
        ("lookup", 0.5),
    )


@dataclass(frozen=True)
class ProjectionConfig:
    """Every parameter that influences the `item_memory` projection."""

    desired_retention: float = 0.85
    alpha: float = 0.3
    decay: float = 0.9
    evidence_weights: tuple[tuple[str, float], ...] = field(default_factory=_default_weights)
    slip_mastery_threshold: float = 0.75
    slip_min_n_eff: float = 3.0
    uncertain_confidence: float = 0.5
    model_version: str = "m1"

    def weight(self, name: str) -> float:
        return dict(self.evidence_weights)[name]

    @property
    def version(self) -> str:
        """Short hash of all fields; stored as `projection_version`."""
        blob = json.dumps(asdict(self), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class LearnerSettings:
    weekly_new_lemmas: int = 20
    weekly_new_grammar: int = 2
    desired_retention: float = 0.85
    review_cap: int = 15
    new_per_session: int = 5
