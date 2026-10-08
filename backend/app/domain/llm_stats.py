"""Aggregation of LLM call timings (design: docs/design/M7-observability.md §2).

Pure functions over plain values, shared by the `llm-stats` command and the latency block of the
grader evaluation. Durations are milliseconds unless the name says otherwise.
"""

import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


def percentile(values: Sequence[float], q: float) -> float | None:
    """The `q`-quantile (0..1) with linear interpolation; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def tokens_per_s(output_tokens: int | None, ttfb_ms: int | None) -> float | None:
    """Output throughput as displayed: output tokens over the time to first byte (for
    non-streaming calls that is queue time plus the whole generation)."""
    if not output_tokens or not ttfb_ms:
        return None
    return output_tokens / (ttfb_ms / 1000)


@dataclass(frozen=True)
class CallStat:
    """What the aggregation needs of one call (an `llm_calls` row or a `CallRecord`)."""

    task: str
    provider: str
    model: str
    latency_ms: int
    error: bool = False
    ttfb_ms: int | None = None
    retry_wait_ms: int | None = None
    attempts: int | None = None
    http_statuses: tuple[int | None, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    upstream_provider: str | None = None

    @classmethod
    def of(cls, call: Any) -> "CallStat":
        return cls(
            task=call.task,
            provider=call.provider,
            model=call.model,
            latency_ms=call.latency_ms or 0,
            error=bool(call.error),
            ttfb_ms=call.ttfb_ms,
            retry_wait_ms=call.retry_wait_ms,
            attempts=call.attempts,
            http_statuses=tuple(call.http_statuses or ()),
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            reasoning_tokens=call.reasoning_tokens,
            upstream_provider=call.upstream_provider,
        )

    @property
    def retries(self) -> int:
        return max(0, (self.attempts or 1) - 1)

    @property
    def tok_per_s(self) -> float | None:
        return tokens_per_s(self.output_tokens, self.ttfb_ms)


def _present(values: Iterable[float | None]) -> list[float]:
    return [v for v in values if v is not None]


def _mean(values: Iterable[float | None]) -> float | None:
    present = _present(values)
    return statistics.fmean(present) if present else None


def retry_statuses(calls: Iterable[CallStat]) -> dict[str, int]:
    """Status codes of the failed attempts that were followed by a retry (`{"429": 3}`);
    transport failures count as `"none"`."""
    counts: Counter[str] = Counter()
    for call in calls:
        for status in call.http_statuses[:-1]:
            counts["none" if status is None else str(status)] += 1
    return dict(sorted(counts.items()))


def spread(values: Iterable[float | None]) -> dict[str, float | None]:
    present = _present(values)
    return {
        "p50": percentile(present, 0.5),
        "p90": percentile(present, 0.9),
        "max": max(present) if present else None,
    }


@dataclass(frozen=True)
class StatsRow:
    task: str
    provider: str
    model: str
    calls: int
    errors: int
    latency_p50: float | None
    latency_p90: float | None
    latency_max: float | None
    ttfb_p50: float | None
    retries: int
    input_mean: float | None
    output_mean: float | None
    reasoning_mean: float | None
    tok_per_s_median: float | None


def aggregate_calls(calls: Iterable[CallStat]) -> list[StatsRow]:
    """One row per task x provider/model, sorted by task then provider/model."""
    groups: dict[tuple[str, str, str], list[CallStat]] = {}
    for call in calls:
        groups.setdefault((call.task, call.provider, call.model), []).append(call)
    rows = []
    for (task, provider, model), group in sorted(groups.items()):
        latency = spread(c.latency_ms for c in group)
        rates = _present(c.tok_per_s for c in group)
        rows.append(
            StatsRow(
                task=task,
                provider=provider,
                model=model,
                calls=len(group),
                errors=sum(c.error for c in group),
                latency_p50=latency["p50"],
                latency_p90=latency["p90"],
                latency_max=latency["max"],
                ttfb_p50=percentile(_present(c.ttfb_ms for c in group), 0.5),
                retries=sum(c.retries for c in group),
                input_mean=_mean(c.input_tokens for c in group),
                output_mean=_mean(c.output_tokens for c in group),
                reasoning_mean=_mean(c.reasoning_tokens for c in group),
                tok_per_s_median=statistics.median(rates) if rates else None,
            )
        )
    return rows


def latency_block(
    calls: Sequence[CallStat], languagetool_ms: Sequence[float | None] = ()
) -> dict[str, Any]:
    """The "latency" block of an evaluation report (JSON-serializable)."""
    rates = _present(c.tok_per_s for c in calls)
    return {
        "calls": len(calls),
        "total_ms": spread(c.latency_ms for c in calls),
        "ttfb_ms": spread(c.ttfb_ms for c in calls),
        "retry_wait_ms": spread(c.retry_wait_ms for c in calls),
        "languagetool_ms": spread(languagetool_ms),
        "retries": sum(c.retries for c in calls),
        "retry_statuses": retry_statuses(calls),
        "output_tokens_mean": _mean(c.output_tokens for c in calls),
        "reasoning_tokens_mean": _mean(c.reasoning_tokens for c in calls),
        "tokens_per_s_median": statistics.median(rates) if rates else None,
        "upstream_providers": sorted({c.upstream_provider for c in calls if c.upstream_provider}),
    }


def _s(ms: float | None, width: int = 7) -> str:
    return f"{'-':>{width}}" if ms is None else f"{ms / 1000:>{width}.1f}"


def _n(value: float | None, width: int = 6) -> str:
    return f"{'-':>{width}}" if value is None else f"{value:>{width}.0f}"


def format_stats(rows: Sequence[StatsRow]) -> str:
    """The `llm-stats` table (times in seconds)."""
    if not rows:
        return "no LLM calls found"
    head = (
        f"{'task':<18}{'provider/model':<34}{'calls':>6}{'err':>5}{'p50':>7}{'p90':>7}{'max':>7}"
        f"{'ttfb50':>8}{'retry':>6}{'in':>7}{'out':>7}{'reason':>7}{'tok/s':>7}"
    )
    lines = [head, "-" * len(head)]
    for r in rows:
        route = f"{r.provider}/{r.model}"
        route = route if len(route) <= 33 else route[:32] + "…"
        lines.append(
            f"{r.task:<18}{route:<34}{r.calls:>6}{r.errors:>5}{_s(r.latency_p50)}{_s(r.latency_p90)}"
            f"{_s(r.latency_max)}{_s(r.ttfb_p50, 8)}{r.retries:>6}{_n(r.input_mean, 7)}"
            f"{_n(r.output_mean, 7)}{_n(r.reasoning_mean, 7)}{_n(r.tok_per_s_median, 7)}"
        )
    lines.append("\ntimes in seconds; in/out/reason = mean tokens per call; tok/s = out / ttfb")
    return "\n".join(lines)
