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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
