"""Scheduler health: predicted versus observed retention (design: docs/design/M5.md §3). Pure."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.config import ProjectionConfig
from app.domain.selection import MemoryView

BUCKETS = 10


@dataclass(frozen=True)
class RetentionEvent:
    """The part of a non-voided event that matters here."""

    kind: str
    outcome: str | None
    predicted_retrievability: float | None


@dataclass(frozen=True)
class CalibrationBucket:
    low: float
    high: float
    n: int
    predicted: float  # mean predicted retrievability of the bucket
    recalled: int  # outcome "correct"
    assisted: int
    forgotten: int  # outcome "error"
    observed: float  # recalled / n


@dataclass(frozen=True)
class RetentionReport:
    n_reviews: int
    observed_retention: float | None  # recalled / n_reviews; None without data
    target_retention: float
    calibration: tuple[CalibrationBucket, ...]  # non-empty buckets, ascending
    due_now: int


def bucket_index(predicted: float) -> int:
    """0.0-0.1 -> 0 ... 0.9-1.0 -> 9 (1.0 belongs to the last bucket)."""
    return min(max(int(predicted * BUCKETS), 0), BUCKETS - 1)


def retention_report(
    events: Sequence[RetentionEvent],
    memories: Sequence[MemoryView],
    cfg: ProjectionConfig,
    now: datetime | None = None,
) -> RetentionReport:
    """Compare the scheduler's predictions with what happened.

    Only `review` events with a predicted retrievability count (implicit exposures are passive
    and carry no recall attempt). `correct` is a recall, `assisted` is reported separately and
    does not count as recalled, `error` is a lapse. `now` enables the due-backlog count.
    """
    groups: dict[int, list[RetentionEvent]] = {}
    for event in events:
        if (
            event.kind != "review"
            or event.predicted_retrievability is None
            or event.outcome not in ("correct", "assisted", "error")
        ):
            continue
        groups.setdefault(bucket_index(event.predicted_retrievability), []).append(event)

    buckets = []
    for index in sorted(groups):
        group = groups[index]
        recalled = sum(1 for e in group if e.outcome == "correct")
        buckets.append(
            CalibrationBucket(
                low=index / BUCKETS,
                high=(index + 1) / BUCKETS,
                n=len(group),
                predicted=sum(e.predicted_retrievability or 0.0 for e in group) / len(group),
                recalled=recalled,
                assisted=sum(1 for e in group if e.outcome == "assisted"),
                forgotten=sum(1 for e in group if e.outcome == "error"),
                observed=recalled / len(group),
            )
        )
    total = sum(b.n for b in buckets)
    recalled_total = sum(b.recalled for b in buckets)
    due_now = (
        sum(1 for m in memories if m.due is not None and m.due <= now) if now is not None else 0
    )
    return RetentionReport(
        n_reviews=total,
        observed_retention=recalled_total / total if total else None,
        target_retention=cfg.desired_retention,
        calibration=tuple(buckets),
        due_now=due_now,
    )
