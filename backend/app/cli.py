"""Maintenance commands: `python -m app.cli <command>`."""

import argparse
import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.curriculum.importer import import_curriculum
from app.curriculum.loader import CurriculumError, load_curriculum
from app.evals.cases import DEFAULT_CASES_DIR
from app.evals.results import RESULTS_DIR
from app.llm.config import PROVIDERS
from app.main import create_app
from app.services.learner import projection_config, settings_of
from app.store.db import create_db_engine, create_session_factory
from app.store.events import rebuild_projection
from app.store.models import Learner
from app.store.schema_version import schema_status


def _schema_is_current() -> bool:
    """Commands that read or write the app database stop with a clear message (instead of a
    SQL error) when migrations are pending."""
    engine = create_db_engine(get_settings().database_url)
    try:
        status = schema_status(engine)
    finally:
        engine.dispose()
    if not status.up_to_date:
        print(f"error: {status.message()}", file=sys.stderr)
    return status.up_to_date


def _stored_llm_overrides() -> dict[str, Any]:
    """The overrides saved from the Settings page, so the CLI tests the models the app really
    uses. When the database cannot be read, print one note and carry on with the environment."""
    from app.llm.overrides import load_overrides

    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    try:
        status = schema_status(engine)
        if not status.up_to_date:
            print(
                "note: database schema out of date; ignoring the models chosen in Settings",
                file=sys.stderr,
            )
            return {}
        with sessionmaker(bind=engine)() as session:
            return dict(load_overrides(session))
    except Exception as exc:
        print(
            f"note: could not read the models chosen in Settings ({type(exc).__name__}); "
            "using the environment",
            file=sys.stderr,
        )
        return {}
    finally:
        engine.dispose()


def cmd_import_curriculum(args: argparse.Namespace) -> int:
    try:
        curriculum = load_curriculum(Path(args.path))
    except CurriculumError as exc:
        for error in exc.errors:
            print(error, file=sys.stderr)
        print(f"{len(exc.errors)} error(s); nothing imported", file=sys.stderr)
        return 1
    if not _schema_is_current():
        return 1
    with create_session_factory(get_settings())() as session:
        report = import_curriculum(session, curriculum, datetime.now(UTC))
        session.commit()
    print(
        f"imported {len(curriculum.items)} items: {report.added} added, {report.updated} updated, "
        f"{report.unchanged} unchanged, {report.suspended} suspended"
    )
    return 0


def cmd_replay(_args: argparse.Namespace) -> int:
    total = 0
    if not _schema_is_current():
        return 1
    with create_session_factory(get_settings())() as session:
        for learner in session.scalars(select(Learner)):
            total += rebuild_projection(
                session, learner.id, projection_config(settings_of(learner))
            )
        session.commit()
    print(f"rebuilt {total} item_memory rows")
    return 0


def cmd_export_openapi(args: argparse.Namespace) -> int:
    app = create_app(Settings(database_url="sqlite://"))
    path = Path(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {path}")
    return 0


def _load_task_config(value: str | None) -> dict[str, dict]:
    if not value:
        return {}
    text = value if value.lstrip().startswith("{") else Path(value).read_text(encoding="utf-8")
    config = json.loads(text)
    if not isinstance(config, dict):
        raise ValueError("--task-config must be a JSON object {task: {...}}")
    return config


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def eval_progress_printer(stream: Any = None) -> Callable[[Any], None]:
    """One line per graded run on stderr: case, outcome vs expectation, latency, ETA.
    The case id is printed before the (possibly slow) LLM call, the result after it."""
    out = stream or sys.stderr

    def show(p: Any) -> None:
        width = len(str(p.total))
        if p.phase == "start":
            if p.run == 1:
                print(f"grading {p.total} run(s); results stream below (Ctrl+C to stop)", file=out)
            print(f"[{p.run:>{width}}/{p.total}] {p.case_id:<28} ", end="", file=out, flush=True)
            return
        if p.phase == "interrupted":
            print("interrupted", file=out, flush=True)
            return
        latency = f"{p.latency_ms / 1000:5.1f}s" if p.latency_ms is not None else "     -"
        if p.error is not None:
            message = " ".join(p.error.split())
            result = f"FAILED  {message[:110]}{'…' if len(message) > 110 else ''}"
        elif p.overall_ok:
            result = f"ok      {p.predicted_overall}"
        else:
            result = f"MISS    expected {p.expected_overall}, got {p.predicted_overall}"
        from app.evals.runner import format_breakdown

        breakdown = f"  {format_breakdown(p.call, p.lt_ms)}" if p.call is not None else ""
        remaining = (p.elapsed_s / p.run) * (p.total - p.run)
        eta = f"  eta {_duration(remaining)}" if p.run < p.total else ""
        print(f"{latency}  {result}{breakdown}{eta}", file=out, flush=True)

    return show


def cmd_eval_grader(args: argparse.Namespace) -> int:
    from app.evals.cases import CaseFileError, check_against_curriculum, load_cases
    from app.evals.runner import (
        MemoryRecorder,
        format_summary,
        items_of,
        run_eval,
        tags_of,
    )
    from app.llm.factory import LLMConfigError, build_llm_client_with_recorder
    from app.llm.overrides import apply_overrides
    from app.nlp.languagetool import LanguageToolClient

    settings = get_settings()
    # The route chosen in Settings for grading is the base; the flags below go on top of it.
    grader_override = _stored_llm_overrides().get("grade_sentence")
    if grader_override is not None:
        settings = apply_overrides(settings, {"grade_sentence": grader_override})
    try:
        tasks = _load_task_config(args.task_config)
        if grader_override is not None and "grade_sentence" in settings.llm_tasks:
            stored_entry = settings.llm_tasks["grade_sentence"]
            tasks["grade_sentence"] = {**stored_entry, **tasks.get("grade_sentence", {})}
        if args.provider or args.model:
            grader = dict(tasks.get("grade_sentence") or {})
            if args.provider:
                grader["provider"] = args.provider
            if args.model:
                grader["model"] = args.model
            tasks["grade_sentence"] = grader
        update: dict[str, object] = {"llm_tasks": tasks}
        if args.provider:
            # The eval only grades, but the client is built for every task: route them all to
            # the requested provider so no other provider's key is needed.
            update["llm_provider"] = args.provider
            update["llm_model"] = args.model
        settings = settings.model_copy(update=update)
        cases = load_cases(Path(args.cases))
        curriculum = load_curriculum(Path(args.curriculum))
    except (CaseFileError, CurriculumError) as exc:
        for error in exc.errors:
            print(error, file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    items = items_of(curriculum)
    problems = check_against_curriculum(cases, tags_of(items), set(items))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if not cases:
        print(f"no cases in {args.cases}", file=sys.stderr)
        return 1
    trace_file = None
    if args.trace:
        try:
            Path(args.trace).parent.mkdir(parents=True, exist_ok=True)
            trace_file = open(args.trace, "w", encoding="utf-8")  # noqa: SIM115
        except OSError as exc:
            print(f"error: cannot write --trace file: {exc}", file=sys.stderr)
            return 1
    recorder = MemoryRecorder(trace_file)
    try:
        llm = build_llm_client_with_recorder(settings, recorder, allow_fake_fallback=False)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    from app.evals.results import run_metadata, save_report

    languagetool = None if args.no_languagetool else LanguageToolClient(settings.languagetool_url)
    started_at = datetime.now(UTC)
    try:
        report = run_eval(
            cases,
            llm,
            recorder,
            items,
            languagetool=languagetool,
            repeat=max(args.repeat, 1),
            progress=None if args.quiet else eval_progress_printer(),
        )
    finally:
        if trace_file is not None:
            trace_file.close()
    if args.trace:
        print(f"trace: {len(recorder.records)} call(s) written to {args.trace}", file=sys.stderr)
    report = {"run": run_metadata(cases, args.label, started_at), **report}
    print(format_summary(report))
    if not args.no_save:
        saved = save_report(report, Path(args.results_dir))
        print(f"\nsaved: {saved}   (compare runs: python -m app.cli eval-compare)")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    if report["summary"].get("interrupted"):
        return 130
    if report["summary"]["failed_runs"]:
        print("some runs failed: the metrics are incomplete", file=sys.stderr)
        return 2
    fp = report["summary"]["false_positive_rate"]
    if fp is not None and fp > args.max_fp:
        print(f"false-positive rate {fp:.3f} exceeds --max-fp {args.max_fp}", file=sys.stderr)
        return 1
    return 0


def cmd_eval_compare(args: argparse.Namespace) -> int:
    from app.evals.results import format_case_diff, format_runs_table, load_runs, select_runs

    runs = load_runs(Path(args.results_dir))
    try:
        chosen = select_runs(runs, args.runs) if args.runs else runs[-args.last :]
    except (LookupError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not chosen:
        print(f"no saved runs in {args.results_dir} (run eval-grader first)")
        return 0
    print(format_runs_table(chosen))
    if len(chosen) == 2:
        print()
        print(format_case_diff(*chosen))
    elif args.runs is None or len(args.runs) != 2:
        print("\ntip: name two runs (any unique part of the file name) to see per-case differences")
    return 0


def cmd_export_contests(args: argparse.Namespace) -> int:
    from app.services.contest_export import export_contests

    if not _schema_is_current():
        return 1
    with create_session_factory(get_settings())() as session:
        written, skipped = export_contests(session, Path(args.directory))
    print(f"wrote {written} draft case(s) to {args.directory} ({skipped} skipped)")
    return 0


def _load_optimizer():  # type: ignore[no-untyped-def]
    """The py-fsrs optimizer class (needs the `optimizer` dependency group: PyTorch)."""
    try:
        from fsrs import Optimizer
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "the FSRS optimizer is not installed. It needs PyTorch and is not part of the "
            "default install: run `uv sync --group optimizer` "
            '(or `uv pip install "fsrs[optimizer]"`).'
        ) from exc
    return Optimizer


def cmd_optimize_fsrs(args: argparse.Namespace) -> int:
    from app.domain.optimize import build_review_logs
    from app.services.learner import update_settings
    from app.store.events import to_event_data
    from app.store.models import LearningEvent

    if not _schema_is_current():
        return 1
    with create_session_factory(get_settings())() as session:
        learner = session.get(Learner, 1)
        if learner is None:
            print("no learner set up yet", file=sys.stderr)
            return 1
        cfg = projection_config(settings_of(learner))
        grouped: dict[tuple[str, str], list] = {}
        for event in session.scalars(
            select(LearningEvent)
            .where(LearningEvent.learner_id == learner.id, LearningEvent.voided_by.is_(None))
            .order_by(LearningEvent.ts, LearningEvent.id)
        ):
            grouped.setdefault((event.item_id, event.facet), []).append(to_event_data(event))
        logs = build_review_logs(grouped, cfg)
        if len(logs) < args.min_reviews:
            print(
                f"refusing to optimize: {len(logs)} reviews, at least {args.min_reviews} needed "
                "(--min-reviews)",
                file=sys.stderr,
            )
            return 1
        try:
            optimizer_class = _load_optimizer()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        optimizer = optimizer_class(logs)
        old = list(cfg.fsrs_parameters) if cfg.fsrs_parameters else None
        if old is None:
            from fsrs import Scheduler

            old = list(Scheduler().parameters)
        new = [float(p) for p in optimizer.compute_optimal_parameters()]
        loss_old = optimizer._compute_batch_loss(parameters=old)
        loss_new = optimizer._compute_batch_loss(parameters=new)
        print(f"{len(logs)} reviews; log-loss {loss_old:.4f} -> {loss_new:.4f}")
        print("parameters:", json.dumps([round(p, 4) for p in new]))
        if not args.apply:
            print("not applied (use --apply)")
            return 0
        if loss_new >= loss_old:
            print("the fitted parameters are not better: nothing applied", file=sys.stderr)
            return 1
        update_settings(session, learner, {"fsrs_parameters": new})
        print("applied; item_memory was rebuilt with the new parameters")
    return 0


KEY_PREFIXES = {"anthropic": "sk-ant-", "openai": "sk-", "openrouter": "sk-or-"}
KEY_VARS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
}


def describe_key(provider: str, raw: str | None) -> list[str]:
    """Human-readable facts about a key as the app receives it, without revealing it."""
    if raw is None:
        return ["not set"]
    cleaned = raw.strip().strip("\"'").strip()
    facts = [f"set, {len(cleaned)} characters, starts with {cleaned[:6]!r}…"]
    if raw != raw.strip():
        facts.append("had surrounding whitespace or a line ending (now removed automatically)")
    if raw.strip()[:1] in ("'", '"'):
        facts.append("was wrapped in quotes (now removed automatically)")
    if any(c.isspace() for c in cleaned):
        facts.append("PROBLEM: contains spaces inside: paste the key again")
    if not cleaned.isascii():
        facts.append("PROBLEM: contains non-ASCII characters: paste the key again")
    if "<" in cleaned or "..." in cleaned or "…" in cleaned:
        facts.append("PROBLEM: looks like a placeholder, not a real key")
    prefix = KEY_PREFIXES.get(provider)
    if prefix and not cleaned.startswith(prefix):
        facts.append(f"PROBLEM: {provider} keys normally start with {prefix!r}")
    return facts


def cmd_check_llm(args: argparse.Namespace) -> int:
    """Show which provider, model and key each task would use, then (with --call) make one
    tiny real request. Keys are never printed, only their length and first characters."""
    import os
    import time

    from app.domain.llm_stats import percentile
    from app.evals.runner import failure_hint, format_breakdown
    from app.llm.calls import CallRecord
    from app.llm.client import LLMError
    from app.llm.factory import LLMConfigError, build_llm_client_with_recorder, resolve_routes
    from app.llm.overrides import apply_overrides
    from app.llm.types import GlossRequest

    overrides = _stored_llm_overrides()
    settings = apply_overrides(get_settings(), overrides)
    try:
        routes = resolve_routes(settings)
    except LLMConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1
    print("task routing:")
    for task, cfg in routes.items():
        mark = "  (Settings)" if task in overrides else ""
        print(f"  {task:<18} {cfg.provider}/{cfg.model}  ({cfg.structured_output} output){mark}")
    problems = False
    for provider in sorted({str(cfg.provider) for cfg in routes.values()} - {"fake"}):
        print(f"\n{provider} key:")
        variables = KEY_VARS[provider]
        if not any(os.environ.get(var, "").strip() for var in variables):
            problems = True
            print(f"  PROBLEM: {' / '.join(variables)} is not set in this environment")
            others = [
                v for p, vs in KEY_VARS.items() if p != provider for v in vs if os.environ.get(v)
            ]
            if others:
                print(f"  (set instead: {', '.join(others)}: is the key under the wrong name?)")
            continue
        for var in variables:
            facts = describe_key(provider, os.environ.get(var))
            problems |= any(f.startswith("PROBLEM") for f in facts)
            print(f"  {var}: " + "; ".join(facts))
    if not args.call:
        print("\nrun with --call to make one small real request (costs a fraction of a cent)")
        return 1 if problems else 0

    repeat = max(args.repeat, 1)
    print(f"\ntest call (gloss task){f', {repeat} times' if repeat > 1 else ''}:")
    records: list[CallRecord] = []
    try:
        llm = build_llm_client_with_recorder(
            settings, lambda r: records.append(r), allow_fake_fallback=False
        )
    except LLMConfigError as exc:
        print(f"  configuration error: {exc}", file=sys.stderr)
        return 1
    request = GlossRequest(
        word="Haus",
        lemma="Haus",
        sentence="Das Haus ist groß.",
        level="A2",
        explanation_language="it",
    )
    failed = 0
    totals: list[int] = []
    for number in range(1, repeat + 1):
        before = len(records)
        started = time.monotonic()
        try:
            gloss = llm.gloss(request)
        except LLMError as exc:
            failed += 1
            print(f"  #{number} FAILED: {exc}")
            if hint := failure_hint(str(exc)):
                print(f"      hint: {hint}")
        else:
            elapsed = int((time.monotonic() - started) * 1000)
            totals.append(elapsed)
            print(f"  #{number} OK in {elapsed} ms: Haus = {gloss.translation!r}")
        if len(records) > before:
            print(f"      {format_breakdown(records[-1])}")
            if records[-1].upstream_provider:
                print(f"      upstream provider: {records[-1].upstream_provider}")
    if len(totals) > 1:
        median = percentile(totals, 0.5) or 0.0
        print(f"  p50 {median / 1000:.2f}s over {len(totals)} successful calls")
    return 1 if failed else 0


_SINCE = re.compile(r"^(\d+)([mhd])$")


def parse_since(value: str) -> timedelta:
    """`30m`, `1h`, `2d` -> timedelta."""
    match = _SINCE.match(value.strip())
    if not match:
        raise ValueError(f"--since must look like 30m, 1h or 2d (got {value!r})")
    unit = {"m": "minutes", "h": "hours", "d": "days"}[match.group(2)]
    return timedelta(**{unit: int(match.group(1))})


def cmd_llm_stats(args: argparse.Namespace) -> int:
    """Per task and provider/model: latency, time to first byte, retries and tokens of the
    calls logged in `llm_calls`."""
    from app.domain.llm_stats import CallStat, aggregate_calls, format_stats
    from app.store.models import LLMCall

    try:
        since = parse_since(args.since) if args.since else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    query = select(LLMCall).order_by(LLMCall.id.desc()).limit(max(args.last, 1))
    if args.task:
        query = query.where(LLMCall.task == args.task)
    if since is not None:
        query = query.where(LLMCall.ts >= datetime.now(UTC) - since)
    if not _schema_is_current():
        return 1
    with create_session_factory(get_settings())() as session:
        calls = [CallStat.of(row) for row in session.scalars(query)]
    scope = f"last {args.last} call(s)" + (f" of task {args.task}" if args.task else "")
    print(f"{scope}{f' in the last {args.since}' if since else ''}: {len(calls)} found\n")
    print(format_stats(aggregate_calls(calls)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import-curriculum", help="validate and import the curriculum")
    p_import.add_argument("--path", default="../curriculum/de")
    p_import.set_defaults(func=cmd_import_curriculum)

    p_replay = sub.add_parser("replay", help="rebuild item_memory from the event log")
    p_replay.set_defaults(func=cmd_replay)

    p_openapi = sub.add_parser("export-openapi", help="write the OpenAPI schema as JSON")
    p_openapi.add_argument("path")
    p_openapi.set_defaults(func=cmd_export_openapi)

    p_eval = sub.add_parser(
        "eval-grader",
        help="measure the grader on the annotated cases (costs money with a real key)",
    )
    p_eval.add_argument("--cases", default=str(DEFAULT_CASES_DIR))
    p_eval.add_argument("--curriculum", default="../curriculum/de")
    p_eval.add_argument(
        "--task-config", help="JSON (or a file) overriding the LLM task config, as LLMLL_LLM_TASKS"
    )
    p_eval.add_argument(
        "--provider",
        choices=PROVIDERS,
        help="provider for grade_sentence (overrides the configuration); recorded in the report",
    )
    p_eval.add_argument(
        "--model", help="model for grade_sentence (overrides the configuration); recorded too"
    )
    p_eval.add_argument("--repeat", type=int, default=1, help="runs per case (consistency)")
    p_eval.add_argument("--quiet", action="store_true", help="no per-case progress lines")
    p_eval.add_argument(
        "--label", help="short note saved with the run, e.g. 'prompt v2' (also in the file name)"
    )
    p_eval.add_argument(
        "--results-dir",
        default=str(RESULTS_DIR),
        help="where every run is saved (default: backend/evals/results)",
    )
    p_eval.add_argument("--no-save", action="store_true", help="don't save this run")
    p_eval.add_argument("--out", help="also write the JSON report to this path")
    p_eval.add_argument(
        "--trace",
        metavar="FILE",
        help="write every LLM call (full request and response, timings, tokens) to FILE, one "
        "JSON object per line (large; for offline inspection)",
    )
    p_eval.add_argument(
        "--max-fp", type=float, default=0.05, help="max false-positive rate on correct answers"
    )
    p_eval.add_argument(
        "--no-languagetool",
        action="store_true",
        help="do not query LanguageTool; use the matches recorded in the cases",
    )
    p_eval.set_defaults(func=cmd_eval_grader)

    p_cmp = sub.add_parser(
        "eval-compare", help="compare saved eval-grader runs (table; per-case diff for two runs)"
    )
    p_cmp.add_argument(
        "runs", nargs="*", default=None, help="runs to compare: file paths or unique name parts"
    )
    p_cmp.add_argument("--last", type=int, default=20, help="without runs: show the last N")
    p_cmp.add_argument("--results-dir", default=str(RESULTS_DIR))
    p_cmp.set_defaults(func=cmd_eval_compare)

    p_contests = sub.add_parser(
        "export-contests", help="write resolved contests as draft evaluation cases"
    )
    p_contests.add_argument("directory")
    p_contests.set_defaults(func=cmd_export_contests)

    p_check = sub.add_parser(
        "check-llm", help="show provider/model/key per task; --call makes one test request"
    )
    p_check.add_argument("--call", action="store_true", help="make one small real request")
    p_check.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="with --call: repeat the request N times and show the p50 (isolates network and "
        "queue time from generation time)",
    )
    p_check.set_defaults(func=cmd_check_llm)

    p_stats = sub.add_parser(
        "llm-stats", help="latency, retries and tokens per task and model from the llm_calls log"
    )
    p_stats.add_argument("--last", type=int, default=200, help="the last N calls (default 200)")
    p_stats.add_argument("--task", help="only this task, e.g. grade_sentence")
    p_stats.add_argument("--since", help="only calls newer than this: 30m, 1h, 2d")
    p_stats.set_defaults(func=cmd_llm_stats)

    p_opt = sub.add_parser("optimize-fsrs", help="fit FSRS parameters to the review history")
    p_opt.add_argument("--min-reviews", type=int, default=1000)
    p_opt.add_argument("--apply", action="store_true", help="store the parameters and replay")
    p_opt.set_defaults(func=cmd_optimize_fsrs)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
