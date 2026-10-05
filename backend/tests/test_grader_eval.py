# ruff: noqa: F811
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.cli import main as cli_main
from app.config import Settings, get_settings
from app.curriculum.loader import load_curriculum
from app.domain.grader_eval import (
    Expectation,
    ExpectedError,
    PredictedError,
    Prediction,
    Signature,
    aggregate,
    consistency,
    score_case,
    span_overlaps,
)
from app.evals.cases import (
    DEFAULT_CASES_DIR,
    CaseFileError,
    check_against_curriculum,
    load_cases,
)
from app.evals.runner import items_of, tags_of
from app.services.contest_export import export_contests
from app.store.db import create_session_factory

from .test_api_production import answer, api, lt, start, stub  # noqa: F401

CURRICULUM = Path(__file__).resolve().parents[2] / "curriculum" / "de"
ANSWER = "Ich habe nach Rom gefahren."


def exp(item="gram:perfekt", span="habe", tags=("wrong-auxiliary",)):
    return ExpectedError(item, span, tags)


def pred(item="gram:perfekt", start=4, end=8, tags=("wrong-auxiliary",)):
    return PredictedError(item, start, end, tags)


# --- the committed case set ----------------------------------------------------------------------


def test_committed_cases_are_valid_and_cover_the_spec() -> None:
    cases = load_cases(DEFAULT_CASES_DIR)
    assert len(cases) >= 60
    by_category: dict[str, int] = {}
    for case in cases:
        by_category[case.category] = by_category.get(case.category, 0) + 1
    assert by_category["correct_variant"] >= 20
    for category in ("correct_reference", "stylistic", "italian_error", "multi_error", "off_task"):
        assert by_category[category] >= 5
    items = items_of(load_curriculum(CURRICULUM))
    assert check_against_curriculum(cases, tags_of(items), set(items)) == []
    # Several error cases carry diagnostic tags (tag accuracy is measurable).
    assert sum(1 for c in cases for e in c.expected.errors if e.diagnostic_tags) >= 20


def write(tmp_path: Path, name: str, data) -> None:
    (tmp_path / name).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def valid_case(**changes) -> dict:
    case = {
        "id": "c1",
        "category": "italian_error",
        "exercise": {
            "type": "translation",
            "instructions": "Traduci in tedesco.",
            "prompt": "Sono andato a Roma.",
            "reference_solutions": ["Ich bin nach Rom gefahren."],
            "targets": [{"item_id": "gram:perfekt"}],
        },
        "answer": ANSWER,
        "expected": {
            "overall": "major_errors",
            "errors": [
                {
                    "item_id": "gram:perfekt",
                    "diagnostic_tags": ["wrong-auxiliary"],
                    "severity": "major",
                    "span_text": "habe",
                }
            ],
        },
    }
    case.update(changes)
    return case


def test_loader_accepts_single_and_list_files_and_skips_drafts(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", valid_case())
    write(tmp_path, "b.yaml", [valid_case(id="c2"), valid_case(id="c3", draft=True)])
    assert [c.id for c in load_cases(tmp_path)] == ["c1", "c2"]
    assert len(load_cases(tmp_path, include_drafts=True)) == 3


@pytest.mark.parametrize(
    "bad",
    [
        {"category": "nonsense"},
        {"expected": {"overall": "correct", "errors": [{"span_text": "habe"}]}},
        {"expected": {"overall": "major_errors", "errors": []}},
        {
            "expected": {
                "overall": "major_errors",
                "errors": [{"item_id": None, "span_text": "not in the answer"}],
            }
        },
        {"category": "correct_variant"},  # implies overall correct
        {"unknown_field": 1},
    ],
)
def test_loader_rejects_invalid_cases(tmp_path: Path, bad: dict) -> None:
    write(tmp_path, "a.yaml", valid_case(**bad))
    with pytest.raises(CaseFileError):
        load_cases(tmp_path)


def test_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", [valid_case(), valid_case()])
    with pytest.raises(CaseFileError, match="duplicate"):
        load_cases(tmp_path)


def test_curriculum_check_reports_unknown_ids_and_tags(tmp_path: Path) -> None:
    case = valid_case()
    case["expected"]["errors"][0]["diagnostic_tags"] = ["made-up"]
    case["exercise"]["targets"].append({"item_id": "lex:does-not-exist"})
    write(tmp_path, "a.yaml", case)
    items = items_of(load_curriculum(CURRICULUM))
    problems = check_against_curriculum(load_cases(tmp_path), tags_of(items), set(items))
    assert any("unknown target" in p for p in problems)
    assert any("made-up" in p for p in problems)


# --- metrics -------------------------------------------------------------------------------------


def test_span_overlap_is_matched_by_text() -> None:
    assert span_overlaps(ANSWER, "habe", 4, 8)
    assert span_overlaps(ANSWER, "habe", 0, 6)  # partial overlap
    assert not span_overlaps(ANSWER, "habe", 9, 13)
    assert span_overlaps(ANSWER, "", 20, 22)  # empty span_text matches anything
    assert span_overlaps(ANSWER, "HABE", 4, 8)  # case-insensitive fallback
    assert not span_overlaps(ANSWER, "missing", 0, 26)


def test_score_perfect_prediction() -> None:
    expected = Expectation("major_errors", (exp(),))
    s = score_case(ANSWER, expected, Prediction("major_errors", (pred(),)))
    assert (s.overall_ok, s.true_positives, s.predicted_errors, s.tag_ok, s.tag_total) == (
        True,
        1,
        1,
        1,
        1,
    )
    assert not s.flagged


def test_score_wrong_item_counts_as_span_match_only() -> None:
    expected = Expectation("major_errors", (exp(),))
    s = score_case(ANSWER, expected, Prediction("major_errors", (pred(item="gram:praesens"),)))
    assert s.true_positives == 0 and s.span_matches == 1


def test_score_null_item_matches_null_item() -> None:
    expected = Expectation("minor_errors", (ExpectedError(None, "habe"),))
    s = score_case(ANSWER, expected, Prediction("minor_errors", (pred(item=None, tags=()),)))
    assert s.true_positives == 1


def test_score_missed_and_spurious_errors() -> None:
    expected = Expectation("major_errors", (exp(),))
    s = score_case(ANSWER, expected, Prediction("major_errors", (pred(start=18, end=26),)))
    assert s.true_positives == 0 and s.predicted_errors == 1 and s.expected_errors == 1


def test_matching_is_one_to_one() -> None:
    expected = Expectation("major_errors", (exp(span=""), exp(span="")))
    s = score_case(ANSWER, expected, Prediction("major_errors", (pred(),)))
    assert s.true_positives == 1  # one prediction cannot satisfy two expectations


def test_tag_accuracy_requires_all_expected_tags() -> None:
    expected = Expectation("major_errors", (exp(tags=("a", "b")),))
    full = score_case(ANSWER, expected, Prediction("major_errors", (pred(tags=("a", "b", "c")),)))
    partial = score_case(ANSWER, expected, Prediction("major_errors", (pred(tags=("a",)),)))
    assert (full.tag_ok, partial.tag_ok, full.tag_total) == (1, 0, 1)


def test_false_positive_rate_on_correct_answers() -> None:
    expected = Expectation("correct")
    clean = score_case(ANSWER, expected, Prediction("correct"))
    flagged_overall = score_case(ANSWER, expected, Prediction("minor_errors"))
    flagged_error = score_case(ANSWER, expected, Prediction("correct", (pred(),)))
    assert not clean.flagged and flagged_overall.flagged and flagged_error.flagged
    metrics = aggregate([clean, flagged_overall, flagged_error, clean])
    assert metrics.false_positive_rate == 0.5 and metrics.n_correct_cases == 4
    # A wrong answer never enters the false-positive rate.
    wrong = score_case(ANSWER, Expectation("major_errors", (exp(),)), Prediction("correct"))
    assert not wrong.expects_correct and aggregate([wrong]).false_positive_rate is None


def test_aggregate_precision_recall_f1() -> None:
    hit = score_case(
        ANSWER, Expectation("major_errors", (exp(),)), Prediction("major_errors", (pred(),))
    )
    miss = score_case(ANSWER, Expectation("major_errors", (exp(),)), Prediction("correct"))
    spurious = score_case(ANSWER, Expectation("correct"), Prediction("minor_errors", (pred(),)))
    m = aggregate([hit, miss, spurious])
    assert m.precision == pytest.approx(1 / 2)  # 1 of 2 predicted errors
    assert m.recall == pytest.approx(1 / 2)  # 1 of 2 expected errors
    assert m.f1 == pytest.approx(1 / 2)
    assert m.overall_accuracy == pytest.approx(1 / 3)
    assert m.attribution_accuracy == 1.0
    empty = aggregate([])
    assert empty.precision is None and empty.f1 is None


def test_consistency() -> None:
    same = [Signature("correct"), Signature("correct")]
    assert consistency(same) == (1.0, 1.0)
    mixed = [Signature("correct"), Signature("correct"), Signature("major_errors", ("a",))]
    overall, items = consistency(mixed)
    assert overall == pytest.approx(1 / 3) and items == pytest.approx(1 / 3)
    assert consistency([Signature("correct")]) is None


# --- eval-grader end to end ----------------------------------------------------------------------


@pytest.fixture
def fake_llm_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLMLL_LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLMLL_DATABASE_URL", f"sqlite:///{tmp_path / 'unused.db'}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def three_cases(tmp_path: Path) -> Path:
    directory = tmp_path / "cases"
    directory.mkdir()
    all_cases = {c.id: c for c in load_cases(DEFAULT_CASES_DIR)}
    chosen = [all_cases[i] for i in ("ref-01", "ita-10", "off-02")]
    write(directory, "three.yaml", [json.loads(c.model_dump_json()) for c in chosen])
    return directory


def test_eval_grader_end_to_end_with_fake_llm(fake_llm_env, tmp_path: Path, capsys) -> None:
    out = tmp_path / "report.json"
    code = cli_main(
        [
            "eval-grader",
            "--cases",
            str(three_cases(tmp_path)),
            "--curriculum",
            str(CURRICULUM),
            "--no-languagetool",
            "--repeat",
            "2",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "false-positive rate" in printed and "italian_error" in printed
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["config"]["cases"] == 3 and report["config"]["llm"] == "fake"
    assert report["config"]["languagetool"] == "recorded"
    assert len(report["cases"]) == 3 and all(len(c["runs"]) == 2 for c in report["cases"])
    summary = report["summary"]
    assert summary["n"] == 6 and summary["failed_runs"] == 0
    # The fake grader accepts the reference answer and rejects everything else.
    assert summary["false_positive_rate"] == 0.0 and summary["n_correct_cases"] == 2
    assert summary["consistency_overall"] == 1.0
    assert report["cost"]["calls"] == 6


def test_eval_grader_fails_above_max_fp(fake_llm_env, tmp_path: Path) -> None:
    # The fake grader marks an answer that differs from the reference as wrong: a false positive.
    directory = tmp_path / "cases"
    directory.mkdir()
    case = {c.id: c for c in load_cases(DEFAULT_CASES_DIR)}["var-01"]
    write(directory, "v.yaml", json.loads(case.model_dump_json()))
    args = ["eval-grader", "--cases", str(directory), "--curriculum", str(CURRICULUM)]
    assert cli_main([*args, "--no-languagetool"]) == 1
    assert cli_main([*args, "--no-languagetool", "--max-fp", "1.0"]) == 0


def test_eval_grader_rejects_invalid_cases(fake_llm_env, tmp_path: Path) -> None:
    write(tmp_path, "bad.yaml", valid_case(category="nonsense"))
    assert cli_main(["eval-grader", "--cases", str(tmp_path), "--curriculum", str(CURRICULUM)]) == 1


def test_eval_grader_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["eval-grader", "--help"])
    assert exc.value.code == 0
    assert "--max-fp" in capsys.readouterr().out


# --- export-contests -----------------------------------------------------------------------------


def test_export_contests_writes_loadable_drafts(
    api: TestClient, migrated_settings: Settings, tmp_path: Path
) -> None:
    session = start(api)
    _intro, first, _second = session["cards"]
    ready = api.post(f"/api/exercises/{first['exercise_id']}/prepare").json()
    wrong = answer(api, session, ready, "Das Tisch")
    resp = api.post(f"/api/evaluations/{wrong['evaluation_id']}/contest", json={"reason": "ok"})
    assert resp.status_code == 200, resp.text

    out = tmp_path / "drafts"
    with create_session_factory(migrated_settings)() as db:
        assert export_contests(db, out) == (1, 0)
        assert export_contests(db, out) == (0, 1)  # existing files are kept
    assert load_cases(out) == []  # drafts are skipped by default
    (draft,) = load_cases(out, include_drafts=True)
    assert draft.draft and draft.id.startswith("contest-") and draft.answer == "Das Tisch"
    assert draft.expected.overall == "correct" and draft.expected.errors == []  # accept_all
    assert draft.exercise.reference_solutions and draft.category == "correct_variant"
    assert "DRAFT" in draft.notes


def test_cli_export_contests(
    api: TestClient, migrated_settings: Settings, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("LLMLL_DATABASE_URL", migrated_settings.database_url)
    get_settings.cache_clear()
    try:
        assert cli_main(["export-contests", str(tmp_path / "d")]) == 0
    finally:
        get_settings.cache_clear()
    assert "wrote 0 draft case(s)" in capsys.readouterr().out
