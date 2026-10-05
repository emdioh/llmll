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
    production_slots: int = 2


@dataclass(frozen=True)
class ReconcileConfig:
    """Parameters of the LLM / LanguageTool reconciliation (design: M2 §3.3)."""

    lt_agree_confidence: float = 0.9
    lt_disagree_factor: float = 0.8
    lt_disagree_below: float = 0.8
    unmatched_lt_confidence: float = 0.7
    off_task_confidence: float = 0.5
    model_version: str = "r1"

    @property
    def version(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True)
        return f"{self.model_version}-{hashlib.sha256(blob.encode()).hexdigest()[:8]}"


@dataclass(frozen=True)
class ReadingConfig:
    """Parameters of the reading flow (design: M3 §3, §5)."""

    coverage_target: float = 0.95
    max_simplify_attempts: int = 3
    max_implicit_per_text: int = 30
    max_candidate_words: int = 10
    max_source_chars: int = 20000
    long_text_words: int = 333
    long_text_max_words: int = 400
    generated_words: int = 150
    seed_lemmas: int = 15


@dataclass(frozen=True)
class PlacementConfig:
    """Parameters of the initial assessment (design: M4 §3)."""

    vocab_per_band: int = 10
    grammar_count: int = 4
    # Word-order stages (R§5) with the declared level from which they are always tested.
    word_order_stages: tuple[tuple[str, str], ...] = (
        ("gram:svo-word-order", "A1"),
        ("gram:adverb-fronting", "A1"),
        ("gram:verbal-bracket", "A1"),
        ("gram:inversion-v2", "A2"),
        ("gram:verb-final-subordinate", "B1"),
    )
    # At least this many grammar slots are kept for points that are not word-order stages.
    min_other_grammar: int = 2
    raise_threshold: float = 0.7
    lower_threshold: float = 0.4
    min_band_results: int = 4
