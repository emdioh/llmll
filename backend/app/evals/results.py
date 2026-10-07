"""Saved grader-evaluation runs: where they live, what identifies them, how to compare them.

Every `eval-grader` run is saved as one JSON report in `evals/results/` (see
`evals/results/README.md`). The report's `run` block records what is needed to compare runs
fairly later: when it ran, an optional label, the git commit, and a fingerprint of the case set
(runs are only directly comparable when the fingerprints match).
"""

import hashlib
import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.evals.cases import GraderCase

RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"
_BACKEND_DIR = Path(__file__).resolve().parents[2]


def cases_fingerprint(cases: Sequence[GraderCase]) -> str:
    """Short hash of the case set (ids, exercises, answers, expectations; notes excluded)."""
    canonical = sorted(
        (c.model_dump(mode="json", exclude={"notes"}) for c in cases), key=lambda c: c["id"]
    )
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
    return digest[:12]


def git_state(cwd: Path = _BACKEND_DIR) -> dict[str, Any]:
    """`{"commit": short sha | None, "dirty": bool | None}`; never fails."""

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5, check=True
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip()

    commit = git("rev-parse", "--short", "HEAD")
    status = git("status", "--porcelain")
    return {"commit": commit, "dirty": None if status is None else bool(status)}


def run_metadata(
    cases: Sequence[GraderCase], label: str | None, started_at: datetime
) -> dict[str, Any]:
    return {
        "started_at": started_at.astimezone(UTC).isoformat(timespec="seconds"),
        "label": label,
        "git": git_state(),
        "cases_fingerprint": cases_fingerprint(cases),
        "n_cases": len(cases),
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "run"


def result_filename(report: dict[str, Any]) -> str:
    """`<UTC timestamp>_<provider-model>[_<label>][_partial].json`: sorts chronologically."""
    run, cfg, summary = report["run"], report["config"], report["summary"]
    stamp = datetime.fromisoformat(run["started_at"]).strftime("%Y%m%d-%H%M%S")
    parts = [stamp, _slug(cfg.get("grader") or cfg.get("llm") or "unknown")]
    if run.get("label"):
        parts.append(_slug(run["label"]))
    if summary.get("interrupted") or summary.get("aborted"):
        parts.append("partial")
    return "_".join(parts) + ".json"


def save_report(report: dict[str, Any], directory: Path = RESULTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / result_filename(report)
    counter = 2
    while path.exists():  # two runs started in the same second
        path = path.with_name(f"{path.stem}-{counter}.json")
        counter += 1
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class SavedRun:
    path: Path
    report: dict[str, Any]

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def started_at(self) -> str:
        return str(self.report.get("run", {}).get("started_at", ""))


def load_runs(directory: Path = RESULTS_DIR) -> list[SavedRun]:
    """All readable reports in `directory`, oldest first. Unreadable files are skipped."""
    runs = []
    for path in sorted(directory.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(report, dict) and "summary" in report and "config" in report:
            runs.append(SavedRun(path, report))
    return sorted(runs, key=lambda r: (r.started_at, r.name))


def select_runs(runs: Sequence[SavedRun], selectors: Sequence[str]) -> list[SavedRun]:
    """Pick runs by file path, file name, or a unique substring of the name."""
    chosen = []
    for selector in selectors:
        path = Path(selector)
        if path.suffix == ".json" and path.is_file():
            chosen.append(SavedRun(path, json.loads(path.read_text(encoding="utf-8"))))
            continue
        matches = [r for r in runs if selector in r.name]
        if len(matches) != 1:
            found = ", ".join(r.name for r in matches) or "none"
            raise LookupError(f"{selector!r} must match exactly one saved run (matches: {found})")
        chosen.append(matches[0])
    return chosen


# --- comparison --------------------------------------------------------------------------------

_METRICS = (
    ("overall acc", "overall_accuracy", True),
    ("FP rate", "false_positive_rate", True),
    ("precision", "precision", True),
    ("recall", "recall", True),
    ("F1", "f1", True),
    ("attrib", "attribution_accuracy", True),
    ("tags", "tag_accuracy", True),
    ("consist", "consistency_overall", True),
)


def _pct(value: Any) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _row(run: SavedRun) -> list[str]:
    report = run.report
    cfg, summary, cost = report["config"], report["summary"], report.get("cost", {})
    meta = report.get("run", {})
    tokens = cost.get("tokens", {})
    latency = cost.get("latency_ms_mean")
    git = meta.get("git") or {}
    commit = (git.get("commit") or "-") + ("*" if git.get("dirty") else "")
    return [
        run.name,
        cfg.get("grader") or cfg.get("llm") or "-",
        meta.get("label") or "",
        *(_pct(summary.get(key)) for _, key, _ in _METRICS),
        str(summary.get("failed_runs", 0)),
        f"{tokens.get('input', 0)}/{tokens.get('output', 0)}",
        "-" if latency is None else f"{latency / 1000:.1f}s",
        meta.get("cases_fingerprint") or "-",
        commit,
    ]


def format_runs_table(runs: Sequence[SavedRun]) -> str:
    """One row per run with the headline metrics; flags runs on different case sets."""
    if not runs:
        return "no saved runs"
    header = [
        "run",
        "grader",
        "label",
        *(title for title, _, _ in _METRICS),
        "failed",
        "tokens in/out",
        "latency",
        "cases",
        "commit",
    ]
    rows = [header, *(_row(r) for r in runs)]
    if len(runs) <= 4:
        # Few runs: one line per metric, one column per run (fits a normal terminal).
        rows = [list(column) for column in zip(*rows, strict=True)]
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)) for row in rows]
    lines = [line.rstrip() for line in lines]
    lines.insert(1, "  ".join("-" * w for w in widths))
    fingerprints = {r.report.get("run", {}).get("cases_fingerprint") for r in runs}
    if len(fingerprints) > 1:
        lines.append(
            "\nnote: these runs used different case sets (see 'cases'): metrics are not directly "
            "comparable"
        )
    if any((r.report.get("run", {}).get("git") or {}).get("dirty") for r in runs):
        lines.append("note: * = run from a working tree with uncommitted changes")
    return "\n".join(lines)


def _first_run(case: dict[str, Any]) -> dict[str, Any] | None:
    runs = case.get("runs") or []
    return runs[0] if runs else None


def _describe(run: dict[str, Any] | None) -> str:
    if run is None:
        return "not run"
    if "error" in run:
        return "FAILED"
    status = "ok" if run.get("overall_ok") else "miss"
    errors = len(run.get("errors") or [])
    return f"{status} {run.get('overall')}" + (f" ({errors} err)" if errors else "")


def _ok(run: dict[str, Any] | None) -> bool:
    return bool(run and "error" not in run and run.get("overall_ok"))


def format_case_diff(a: SavedRun, b: SavedRun) -> str:
    """Cases whose outcome (overall label right/wrong, failure, error count) differs."""
    cases_a = {c["id"]: c for c in a.report.get("cases", [])}
    cases_b = {c["id"]: c for c in b.report.get("cases", [])}
    lines, better, worse = [], 0, 0
    for case_id in sorted(cases_a.keys() | cases_b.keys()):
        ra, rb = _first_run(cases_a.get(case_id, {})), _first_run(cases_b.get(case_id, {}))
        if _describe(ra) == _describe(rb):
            continue
        better += not _ok(ra) and _ok(rb)
        worse += _ok(ra) and not _ok(rb)
        expected = (cases_a.get(case_id) or cases_b.get(case_id) or {}).get("expected_overall")
        lines.append(
            f"  {case_id:<14} expected {expected!s:<13} {_describe(ra):<28} -> {_describe(rb)}"
        )
    title = f"per-case differences ({a.name} -> {b.name}):"
    if not lines:
        return f"{title}\n  none: every case has the same outcome"
    summary = f"  {len(lines)} changed: {better} now right, {worse} now wrong"
    return "\n".join([title, *lines, summary])
