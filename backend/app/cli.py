"""Maintenance commands: `python -m app.cli <command>`."""

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import Settings, get_settings
from app.curriculum.importer import import_curriculum
from app.curriculum.loader import CurriculumError, load_curriculum
from app.evals.cases import DEFAULT_CASES_DIR
from app.llm.config import PROVIDERS
from app.main import create_app
from app.services.learner import projection_config, settings_of
from app.store.db import create_session_factory
from app.store.events import rebuild_projection
from app.store.models import Learner


def cmd_import_curriculum(args: argparse.Namespace) -> int:
    try:
        curriculum = load_curriculum(Path(args.path))
    except CurriculumError as exc:
        for error in exc.errors:
            print(error, file=sys.stderr)
        print(f"{len(exc.errors)} error(s); nothing imported", file=sys.stderr)
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
        remaining = (p.elapsed_s / p.run) * (p.total - p.run)
        eta = f"  eta {_duration(remaining)}" if p.run < p.total else ""
        print(f"{latency}  {result}{eta}", file=out, flush=True)

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
    from app.nlp.languagetool import LanguageToolClient

    settings = get_settings()
    try:
        tasks = _load_task_config(args.task_config)
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
    recorder = MemoryRecorder()
    try:
        llm = build_llm_client_with_recorder(settings, recorder, allow_fake_fallback=False)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    languagetool = None if args.no_languagetool else LanguageToolClient(settings.languagetool_url)
    report = run_eval(
        cases,
        llm,
        recorder,
        items,
        languagetool=languagetool,
        repeat=max(args.repeat, 1),
        progress=None if args.quiet else eval_progress_printer(),
    )
    print(format_summary(report))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nwrote {out}")
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


def cmd_export_contests(args: argparse.Namespace) -> int:
    from app.services.contest_export import export_contests

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

    from app.evals.runner import failure_hint
    from app.llm.client import LLMError
    from app.llm.factory import LLMConfigError, build_llm_client_with_recorder, resolve_routes
    from app.llm.types import GlossRequest

    settings = get_settings()
    try:
        routes = resolve_routes(settings)
    except LLMConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1
    print("task routing:")
    for task, cfg in routes.items():
        print(f"  {task:<18} {cfg.provider}/{cfg.model}  ({cfg.structured_output} output)")
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

    print("\ntest call (gloss task):")
    try:
        llm = build_llm_client_with_recorder(settings, lambda _r: None, allow_fake_fallback=False)
    except LLMConfigError as exc:
        print(f"  configuration error: {exc}", file=sys.stderr)
        return 1
    started = time.monotonic()
    try:
        gloss = llm.gloss(
            GlossRequest(
                word="Haus",
                lemma="Haus",
                sentence="Das Haus ist groß.",
                level="A2",
                explanation_language="it",
            )
        )
    except LLMError as exc:
        print(f"  FAILED: {exc}")
        if hint := failure_hint(str(exc)):
            print(f"  hint: {hint}")
        return 1
    elapsed = int((time.monotonic() - started) * 1000)
    print(f"  OK in {elapsed} ms: Haus = {gloss.translation!r}")
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
    p_eval.add_argument("--out", help="write the JSON report here")
    p_eval.add_argument(
        "--max-fp", type=float, default=0.05, help="max false-positive rate on correct answers"
    )
    p_eval.add_argument(
        "--no-languagetool",
        action="store_true",
        help="do not query LanguageTool; use the matches recorded in the cases",
    )
    p_eval.set_defaults(func=cmd_eval_grader)

    p_contests = sub.add_parser(
        "export-contests", help="write resolved contests as draft evaluation cases"
    )
    p_contests.add_argument("directory")
    p_contests.set_defaults(func=cmd_export_contests)

    p_check = sub.add_parser(
        "check-llm", help="show provider/model/key per task; --call makes one test request"
    )
    p_check.add_argument("--call", action="store_true", help="make one small real request")
    p_check.set_defaults(func=cmd_check_llm)

    p_opt = sub.add_parser("optimize-fsrs", help="fit FSRS parameters to the review history")
    p_opt.add_argument("--min-reviews", type=int, default=1000)
    p_opt.add_argument("--apply", action="store_true", help="store the parameters and replay")
    p_opt.set_defaults(func=cmd_optimize_fsrs)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
