"""Latency reporting of eval-grader: progress line, latency block, report fields, eval-compare."""

import io
import json
from pathlib import Path

import pytest

from app.cli import eval_progress_printer
from app.cli import main as cli_main
from app.config import get_settings
from app.curriculum.loader import load_curriculum
from app.domain.llm_stats import CallStat, latency_block, percentile, tokens_per_s
from app.evals.cases import load_cases
from app.evals.results import SavedRun, format_runs_table
from app.evals.runner import (
    MemoryRecorder,
    format_breakdown,
    format_summary,
    items_of,
    run_eval,
)
from app.llm.calls import CallRecord
from app.llm.types import GradeResult

from .test_eval_results import report as saved_report
from .test_grader_eval import CURRICULUM, three_cases


def timed_record(**changes: object) -> CallRecord:
    values: dict = {
        "task": "grade_sentence",
        "prompt_version": "v1",
        "provider": "openrouter",
        "model": "vendor/m",
        "request": {},
        "latency_ms": 12300,
        "attempts": 1,
        "http_statuses": [200],
        "retry_wait_ms": 0,
        "ttfb_ms": 12100,
        "download_ms": 100,
        "overhead_ms": 100,
        "input_tokens": 900,
        "output_tokens": 412,
        "reasoning_tokens": 2140,
        "upstream_provider": "Fireworks",
    }
    values.update(changes)
    return CallRecord(**values)


def test_breakdown_line() -> None:
    line = format_breakdown(timed_record(), lt_ms=200)
    assert line == (
        "llm 12.3s = wait 0.0s + ttfb 12.1s + dl 0.1s | 1 try | out 412 (reasoning 2140) "
        "| 34 tok/s | lt 0.2s"
    )


def test_breakdown_with_a_retry_and_without_http_trace() -> None:
    retried = timed_record(attempts=2, http_statuses=[429, 200], retry_wait_ms=1000)
    assert "wait 1.0s" in format_breakdown(retried) and "2 tries (429,200)" in format_breakdown(
        retried
    )
    untraced = timed_record(
        attempts=None, http_statuses=None, retry_wait_ms=None, ttfb_ms=None, reasoning_tokens=None
    )
    assert format_breakdown(untraced) == "llm 12.3s | out 412"


def test_percentiles_and_throughput() -> None:
    assert percentile([], 0.5) is None and percentile([4], 0.9) == 4
    assert percentile([1, 2, 3, 4], 0.5) == 2.5 and percentile([1, 2, 3, 4, 5], 0.9) == 4.6
    assert tokens_per_s(412, 12100) == pytest.approx(34.05, abs=0.01)
    assert tokens_per_s(None, 1000) is None and tokens_per_s(10, 0) is None


def test_latency_block() -> None:
    calls = [
        CallStat.of(timed_record(latency_ms=1000, ttfb_ms=900)),
        CallStat.of(
            timed_record(
                latency_ms=3000,
                ttfb_ms=2000,
                attempts=3,
                http_statuses=[429, 503, 200],
                retry_wait_ms=800,
                upstream_provider="Together",
                reasoning_tokens=None,
            )
        ),
    ]
    block = latency_block(calls, [100, 300])
    assert block["calls"] == 2 and block["total_ms"] == {"p50": 2000, "p90": 2800, "max": 3000}
    assert block["ttfb_ms"]["p50"] == 1450 and block["languagetool_ms"]["max"] == 300
    assert block["retries"] == 2 and block["retry_statuses"] == {"429": 1, "503": 1}
    assert block["reasoning_tokens_mean"] == 2140 and block["output_tokens_mean"] == 412
    assert block["upstream_providers"] == ["Fireworks", "Together"]
    assert latency_block([])["total_ms"]["p50"] is None


class TimedGrader:
    """Records a traced call per grading, like a real adapter."""

    name = "stub"
    routes = {"grade_sentence": "stub/m"}

    def __init__(self, recorder: MemoryRecorder, fail_first: bool = False) -> None:
        self.recorder = recorder
        self.fail_first = fail_first
        self.calls = 0

    def grade_sentence(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.recorder(timed_record(latency_ms=1000 * self.calls, ttfb_ms=900 * self.calls))
        if self.fail_first and self.calls == 1:
            from app.llm.client import LLMUnavailable

            raise LLMUnavailable("429 rate limited")
        return GradeResult(overall="correct", corrected_sentence=request.answer, feedback="ok")


def run_three(tmp_path: Path, **kwargs):  # type: ignore[no-untyped-def]
    items = items_of(load_curriculum(CURRICULUM))
    cases = load_cases(three_cases(tmp_path))
    recorder = MemoryRecorder()
    events: list = []
    result = run_eval(
        cases, TimedGrader(recorder, **kwargs), recorder, items, progress=events.append
    )
    return result, events, cases


def test_report_has_per_run_timing_and_a_latency_block(tmp_path: Path) -> None:
    result, events, _ = run_three(tmp_path)
    run = result["cases"][0]["runs"][0]
    assert run["ttfb_ms"] == 900 and run["attempts"] == 1 and run["http_statuses"] == [200]
    assert run["reasoning_tokens"] == 2140 and run["upstream_provider"] == "Fireworks"
    assert run["retry_wait_ms"] == 0 and run["download_ms"] == 100
    latency = result["latency"]
    assert latency["calls"] == 3 and latency["total_ms"]["max"] == 3000
    assert latency["upstream_providers"] == ["Fireworks"]
    done = [e for e in events if e.phase == "done"]
    assert all(e.call is not None for e in done)


def test_failed_runs_still_count_in_the_latency_block(tmp_path: Path) -> None:
    result, events, _ = run_three(tmp_path, fail_first=True)
    assert result["summary"]["failed_runs"] == 1 and result["latency"]["calls"] == 3
    failed = next(e for e in events if e.error)
    assert failed.call is not None  # the progress line can show why it was slow


def test_progress_line_and_summary_block(tmp_path: Path) -> None:
    result, events, cases = run_three(tmp_path)
    stream = io.StringIO()
    show = eval_progress_printer(stream)
    for event in events:
        show(event)
    done_line = next(line for line in stream.getvalue().splitlines() if "llm " in line)
    assert "ok" in done_line and "= wait 0.0s + ttfb 0.9s + dl 0.1s | 1 try" in done_line
    assert "out 412 (reasoning 2140)" in done_line and "tok/s" in done_line
    summary = format_summary({"run": {}, **{**result, "config": result["config"]}})
    assert "latency (seconds, 3 call(s))" in summary
    assert (
        "p50" in summary and "retries       0" in summary and "upstream      Fireworks" in summary
    )
    assert "tokens        mean out 412, reasoning 2140" in summary


def test_languagetool_time_is_measured_per_case(tmp_path: Path) -> None:
    class SlowLT:
        def check(self, text: str):  # type: ignore[no-untyped-def]
            import time

            time.sleep(0.02)
            return []

    items = items_of(load_curriculum(CURRICULUM))
    cases = load_cases(three_cases(tmp_path))
    recorder = MemoryRecorder()
    events: list = []
    result = run_eval(
        cases,
        TimedGrader(recorder),
        recorder,
        items,
        languagetool=SlowLT(),  # type: ignore[arg-type]
        progress=events.append,
    )
    times = [c["languagetool_ms"] for c in result["cases"]]
    assert all(t is not None for t in times) and max(times) >= 20  # an empty answer skips the check
    assert result["latency"]["languagetool_ms"]["max"] >= 20
    assert all(e.lt_ms is not None for e in events if e.phase == "done")
    assert "languagetool" in format_summary({"run": {}, **result})


def test_old_reports_have_no_latency_block() -> None:
    old = saved_report()
    assert "latency (seconds" not in format_summary_safe(old)


def format_summary_safe(report: dict) -> str:
    from app.evals.runner import format_latency_block

    return "\n".join(format_latency_block(report.get("latency")))


def test_compare_rows() -> None:
    new = saved_report("new")
    new["latency"] = latency_block(
        [CallStat.of(timed_record(latency_ms=1000, ttfb_ms=900)), CallStat.of(timed_record())]
    )
    table = format_runs_table(
        [SavedRun(Path("old.json"), saved_report("old")), SavedRun(Path("new.json"), new)]
    )
    assert "lat p50" in table and "lat p90" in table and "ttfb p50" in table
    assert "retries" in table and "reasoning" in table
    row = next(line for line in table.splitlines() if line.startswith("lat p50"))
    assert "-" in row and "6.7s" in row  # old run: no data; new run: p50 of 1.0s and 12.3s


def test_cli_trace_file_and_latency_in_the_saved_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLMLL_LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLMLL_DATABASE_URL", f"sqlite:///{tmp_path / 'unused.db'}")
    get_settings.cache_clear()
    trace = tmp_path / "out" / "trace.jsonl"
    out = tmp_path / "report.json"
    code = cli_main(
        [
            "eval-grader",
            "--cases",
            str(three_cases(tmp_path)),
            "--curriculum",
            str(CURRICULUM),
            "--no-languagetool",
            "--trace",
            str(trace),
            "--out",
            str(out),
        ]
    )
    get_settings.cache_clear()
    assert code == 0
    lines = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3 and lines[0]["task"] == "grade_sentence"
    assert "request" in lines[0] and "response" in lines[0] and "latency_ms" in lines[0]
    assert "reasoning_tokens" in lines[0] and "uid" not in lines[0]
    assert "latency" in json.loads(out.read_text(encoding="utf-8"))
    assert "3 call(s) written" in capsys.readouterr().err
