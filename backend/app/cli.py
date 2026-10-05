"""Maintenance commands: `python -m app.cli <command>`."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import Settings, get_settings
from app.curriculum.importer import import_curriculum
from app.curriculum.loader import CurriculumError, load_curriculum
from app.evals.cases import DEFAULT_CASES_DIR
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


def cmd_eval_grader(args: argparse.Namespace) -> int:
    from app.evals.cases import CaseFileError, check_against_curriculum, load_cases
    from app.evals.runner import (
        MemoryRecorder,
        format_summary,
        items_of,
        run_eval,
        tags_of,
    )
    from app.llm.factory import build_llm_client_with_recorder
    from app.nlp.languagetool import LanguageToolClient

    settings = get_settings()
    try:
        settings = settings.model_copy(update={"llm_tasks": _load_task_config(args.task_config)})
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
    llm = build_llm_client_with_recorder(settings, recorder)
    languagetool = None if args.no_languagetool else LanguageToolClient(settings.languagetool_url)
    report = run_eval(
        cases, llm, recorder, items, languagetool=languagetool, repeat=max(args.repeat, 1)
    )
    print(format_summary(report))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nwrote {out}")
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
    p_eval.add_argument("--repeat", type=int, default=1, help="runs per case (consistency)")
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

    p_opt = sub.add_parser("optimize-fsrs", help="fit FSRS parameters to the review history")
    p_opt.add_argument("--min-reviews", type=int, default=1000)
    p_opt.add_argument("--apply", action="store_true", help="store the parameters and replay")
    p_opt.set_defaults(func=cmd_optimize_fsrs)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
