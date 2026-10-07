"""Saving, loading and comparing eval-grader runs."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.cli import main as cli_main
from app.config import get_settings
from app.evals.results import (
    SavedRun,
    cases_fingerprint,
    format_case_diff,
    format_runs_table,
    load_runs,
    result_filename,
    save_report,
    select_runs,
)

from .test_grader_eval import CURRICULUM, three_cases


def report(label: str | None = None, *, ok: bool = True, fp: float = 0.0, partial: bool = False):  # type: ignore[no-untyped-def]
    return {
        "run": {
            "started_at": datetime(2026, 10, 7, 9, 30, 5, tzinfo=UTC).isoformat(),
            "label": label,
            "git": {"commit": "abc1234", "dirty": False},
            "cases_fingerprint": "f00",
            "n_cases": 1,
        },
        "config": {"grader": "openrouter/vendor/model", "llm": "openrouter"},
        "summary": {
            "overall_accuracy": 1.0 if ok else 0.0,
            "false_positive_rate": fp,
            "failed_runs": 0,
            "interrupted": partial,
        },
        "cost": {"tokens": {"input": 10, "output": 5}, "latency_ms_mean": 1500},
        "cases": [
            {
                "id": "ref-01",
                "expected_overall": "correct",
                "runs": [{"overall": "correct" if ok else "major_errors", "overall_ok": ok}],
            }
        ],
    }


def test_filename_is_chronological_and_readable() -> None:
    assert result_filename(report("Prompt v2!")) == (
        "20261007-093005_openrouter-vendor-model_prompt-v2.json"
    )
    assert result_filename(report(partial=True)).endswith("_partial.json")


def test_save_never_overwrites_and_load_sorts(tmp_path: Path) -> None:
    first = save_report(report("a"), tmp_path)
    second = save_report(report("a"), tmp_path)  # same second, same label
    assert first != second and second.name.endswith("-2.json")
    (tmp_path / "notes.json").write_text("{not json")  # ignored
    runs = load_runs(tmp_path)
    assert [r.path for r in runs] == [first, second]


def test_select_runs_by_unique_name_part(tmp_path: Path) -> None:
    save_report(report("baseline"), tmp_path)
    save_report(report("prompt v2"), tmp_path)
    runs = load_runs(tmp_path)
    assert [r.report["run"]["label"] for r in select_runs(runs, ["v2"])] == ["prompt v2"]
    with pytest.raises(LookupError, match="exactly one"):
        select_runs(runs, ["openrouter"])  # ambiguous


def test_table_and_case_diff() -> None:
    a = SavedRun(Path("a.json"), report("before", ok=True))
    b = SavedRun(Path("b.json"), report("after", ok=False, fp=0.5))
    table = format_runs_table([a, b])
    assert "FP rate" in table and "50.0%" in table and "before" in table and "after" in table
    diff = format_case_diff(a, b)
    assert "ref-01" in diff and "1 changed: 0 now right, 1 now wrong" in diff
    assert "none" in format_case_diff(a, a)


def test_table_warns_about_different_case_sets() -> None:
    a = SavedRun(Path("a.json"), report())
    other = report()
    other["run"]["cases_fingerprint"] = "bar"
    assert "different case sets" in format_runs_table([a, SavedRun(Path("b.json"), other)])


def test_fingerprint_ignores_notes_but_not_expectations(tmp_path: Path) -> None:
    from app.evals.cases import load_cases

    cases = load_cases(three_cases(tmp_path))
    base = cases_fingerprint(cases)
    assert cases_fingerprint([c.model_copy(update={"notes": "x"}) for c in cases]) == base
    changed = cases[0].model_copy(update={"answer": cases[0].answer + " Ja."})
    assert cases_fingerprint([changed, *cases[1:]]) != base


def test_eval_grader_saves_every_run_and_eval_compare_reads_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLMLL_LLM_PROVIDER", "fake")
    get_settings.cache_clear()
    results = tmp_path / "results"
    args = [
        "eval-grader",
        "--cases",
        str(three_cases(tmp_path)),
        "--curriculum",
        str(CURRICULUM),
        "--no-languagetool",
        "--quiet",
        "--max-fp",
        "1.0",
        "--results-dir",
        str(results),
    ]
    assert cli_main([*args, "--label", "first"]) == 0
    assert cli_main([*args, "--label", "second"]) == 0
    assert cli_main([*args, "--no-save"]) == 0
    saved = sorted(results.glob("*.json"))
    assert len(saved) == 2
    data = json.loads(saved[0].read_text())
    assert data["run"]["label"] == "first" and data["run"]["n_cases"] == 3
    assert data["run"]["cases_fingerprint"] and "commit" in data["run"]["git"]
    capsys.readouterr()
    assert cli_main(["eval-compare", "--results-dir", str(results), "first", "second"]) == 0
    out = capsys.readouterr().out
    assert "first" in out and "second" in out and "per-case differences" in out
    assert cli_main(["eval-compare", "--results-dir", str(results), "nomatch"]) == 1
    get_settings.cache_clear()
