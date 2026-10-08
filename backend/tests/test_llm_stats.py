"""`llm-stats`: the pure aggregation and the command on a temporary database."""

from datetime import UTC, datetime, timedelta

import pytest

from app.cli import main as cli_main
from app.cli import parse_since
from app.config import Settings, get_settings
from app.domain.llm_stats import CallStat, aggregate_calls, format_stats
from app.llm.calls import CallRecord, SqlCallRecorder
from app.store.db import create_session_factory


def stat(task: str = "gloss", **changes: object) -> CallStat:
    values: dict = {
        "task": task,
        "provider": "openrouter",
        "model": "v/m",
        "latency_ms": 1000,
        "ttfb_ms": 800,
        "attempts": 1,
        "http_statuses": (200,),
        "input_tokens": 100,
        "output_tokens": 40,
    }
    values.update(changes)
    return CallStat(**values)


def test_aggregation_per_task_and_model() -> None:
    rows = aggregate_calls(
        [
            stat(latency_ms=1000, ttfb_ms=800, reasoning_tokens=10),
            stat(latency_ms=3000, ttfb_ms=2000, attempts=2, http_statuses=(429, 200)),
            stat(latency_ms=500, error=True, ttfb_ms=None, output_tokens=None),
            stat("explain", provider="anthropic", model="claude"),
        ]
    )
    assert [(r.task, r.provider, r.model) for r in rows] == [
        ("explain", "anthropic", "claude"),
        ("gloss", "openrouter", "v/m"),
    ]
    gloss = rows[1]
    assert gloss.calls == 3 and gloss.errors == 1 and gloss.retries == 1
    assert gloss.latency_p50 == 1000 and gloss.latency_p90 == 2600 and gloss.latency_max == 3000
    assert gloss.ttfb_p50 == 1400
    assert gloss.input_mean == 100 and gloss.output_mean == 40 and gloss.reasoning_mean == 10
    assert gloss.tok_per_s_median == pytest.approx(35)  # median of 50 and 20 tok/s


def test_calls_without_timing_do_not_break_the_aggregation() -> None:
    (row,) = aggregate_calls(
        [stat(ttfb_ms=None, attempts=None, http_statuses=(), output_tokens=None)]
    )
    assert row.ttfb_p50 is None and row.retries == 0 and row.tok_per_s_median is None
    assert row.output_mean is None and row.reasoning_mean is None
    assert aggregate_calls([]) == [] and format_stats([]) == "no LLM calls found"


def test_table() -> None:
    table = format_stats(aggregate_calls([stat(), stat(latency_ms=2000)]))
    header, rule, row, *_ = table.splitlines()
    assert "p50" in header and "ttfb50" in header and set(rule) == {"-"}
    assert row.startswith("gloss") and "openrouter/v/m" in row and "1.5" in row


def test_parse_since() -> None:
    assert parse_since("30m") == timedelta(minutes=30)
    assert parse_since("1h") == timedelta(hours=1) and parse_since("2d") == timedelta(days=2)
    with pytest.raises(ValueError):
        parse_since("soon")


def test_cli_on_a_temp_database(
    migrated_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    now = datetime.now(UTC)
    clock = [now - timedelta(days=2)]
    recorder = SqlCallRecorder(create_session_factory(migrated_settings), lambda: clock[0])

    def record(task: str, latency: int, age: timedelta, **changes: object) -> None:
        clock[0] = now - age
        recorder(
            CallRecord(
                task=task,
                prompt_version="v1",
                provider="openrouter",
                model="v/m",
                request={},
                latency_ms=latency,
                output_tokens=40,
                attempts=1,
                http_statuses=[200],
                ttfb_ms=latency - 100,
                retry_wait_ms=0,
                reasoning_tokens=7,
                **changes,  # type: ignore[arg-type]
            )
        )

    record("gloss", 4000, timedelta(days=2))
    record("gloss", 2000, timedelta(minutes=10))
    record("explain", 6000, timedelta(minutes=5), error="LLMError: boom")
    monkeypatch.setenv("LLMLL_DATABASE_URL", migrated_settings.database_url)
    get_settings.cache_clear()
    try:
        assert cli_main(["llm-stats"]) == 0
        everything = capsys.readouterr().out
        assert "3 found" in everything and "gloss" in everything and "explain" in everything
        assert cli_main(["llm-stats", "--task", "gloss", "--since", "1h"]) == 0
        recent = capsys.readouterr().out
        assert "1 found" in recent and "explain" not in recent
        assert cli_main(["llm-stats", "--last", "2"]) == 0
        assert "2 found" in capsys.readouterr().out
        assert cli_main(["llm-stats", "--since", "bogus"]) == 1
        assert "--since must look like" in capsys.readouterr().err
    finally:
        get_settings.cache_clear()
