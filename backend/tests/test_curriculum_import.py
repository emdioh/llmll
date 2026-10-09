from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import Settings
from app.curriculum.importer import import_curriculum
from app.curriculum.loader import CurriculumError, load_curriculum
from app.store.db import create_session_factory
from app.store.models import Item, ItemPrerequisite

from .conftest import FIXTURES, import_fixture

REAL = Path(__file__).resolve().parents[2] / "curriculum" / "de"


def test_valid_fixture_loads() -> None:
    items = load_curriculum(FIXTURES / "curriculum").items
    kinds = {i.kind for i in items}
    assert kinds == {"lemma", "grammar", "construction"}
    tisch = next(i for i in items if i.id == "lex:tisch")
    assert tisch.payload["lemma"] == "Tisch"
    assert "id" not in tisch.payload and "level" not in tisch.payload
    assert tisch.interference == {"it_gender": "m"}


def _errors(name: str) -> list[str]:
    with pytest.raises(CurriculumError) as exc:
        load_curriculum(FIXTURES / name)
    return exc.value.errors


def test_duplicate_id_rejected() -> None:
    errors = _errors("curriculum_dup")
    assert any(e.startswith("lexicon/a2.yaml:1:") and "duplicate id lex:tisch" in e for e in errors)


def test_missing_prerequisite_rejected() -> None:
    errors = _errors("curriculum_missing_req")
    assert any(e.startswith("lexicon/a2.yaml:0:") and "lex:nope" in e for e in errors)


def test_cycle_rejected() -> None:
    errors = _errors("curriculum_cycle")
    assert any("cycle" in e and "gram:articles" in e and "gram:cases" in e for e in errors)


def test_schema_errors_reported_all_at_once() -> None:
    errors = _errors("curriculum_schema_error")
    assert any(e.startswith("lexicon/a1.yaml:2:") and "gender" in e for e in errors)
    assert any(e.startswith("lexicon/a1.yaml:4:") and "level" in e for e in errors)


def test_missing_directory() -> None:
    with pytest.raises(CurriculumError):
        load_curriculum(FIXTURES / "does-not-exist")


def test_import_populates_tables(migrated_settings: Settings) -> None:
    report = import_fixture(migrated_settings)
    assert report.added == 17 and report.updated == 0
    with create_session_factory(migrated_settings)() as session:
        tisch = session.get(Item, "lex:tisch")
        assert tisch.frequency_zipf and tisch.frequency_zipf > 3
        assert tisch.payload["translations"]["it"] == "tavolo"
        assert session.get(Item, "gram:articles").frequency_zipf is None
        edges = set(
            session.execute(select(ItemPrerequisite.item_id, ItemPrerequisite.requires_item_id))
        )
        assert ("gram:cases", "gram:articles") in edges and ("lex:fahren", "lex:gehen") in edges


def test_reimport_is_idempotent(migrated_settings: Settings) -> None:
    import_fixture(migrated_settings)
    report = import_fixture(migrated_settings)
    assert (report.added, report.updated, report.suspended, report.unchanged) == (0, 0, 0, 17)
    with create_session_factory(migrated_settings)() as session:
        assert len(session.scalars(select(Item)).all()) == 17
        assert len(session.scalars(select(ItemPrerequisite)).all()) == 3


def test_removed_id_is_suspended_not_deleted(migrated_settings: Settings, tmp_path: Path) -> None:
    import shutil

    import yaml

    root = tmp_path / "c"
    shutil.copytree(FIXTURES / "curriculum", root)
    import_fixture(migrated_settings)
    path = root / "lexicon" / "a1.yaml"
    data = yaml.safe_load(path.read_text())
    data = [d for d in data if d["id"] != "lex:katze"]
    data[0]["translations"]["it"] = "tavolino"
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    with create_session_factory(migrated_settings)() as session:
        report = import_curriculum(session, load_curriculum(root), datetime_now())
        session.commit()
    assert (report.updated, report.suspended) == (1, 1)
    with create_session_factory(migrated_settings)() as session:
        assert session.get(Item, "lex:katze").suspended is True
        assert session.get(Item, "lex:tisch").payload["translations"]["it"] == "tavolino"
        assert session.get(Item, "lex:tisch").suspended is False


def datetime_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def test_real_curriculum_validates() -> None:
    if not REAL.is_dir():
        pytest.skip("curriculum/de does not exist yet")
    curriculum = load_curriculum(REAL)
    assert curriculum.items


def test_extra_root_merged_with_prefixed_source_files() -> None:
    extra = FIXTURES / "curriculum_extra"
    items = load_curriculum(FIXTURES / "curriculum", [extra]).items
    by_id = {i.id: i for i in items}
    assert len(items) == 19
    assert by_id["lex:tisch"].source_file == "lexicon/a1.yaml"
    assert by_id["lex:sofa"].source_file == f"{extra}/lexicon/a1.yaml"
    assert by_id["gram:private"].source_file == f"{extra}/grammar/private.yaml"
    # private items may require main items
    assert by_id["lex:sofa"].requires == ["lex:tisch"]
    assert by_id["gram:private"].requires == ["gram:cases"]


def test_duplicate_id_across_roots_rejected() -> None:
    extra = FIXTURES / "curriculum_extra_dup"
    with pytest.raises(CurriculumError) as exc:
        load_curriculum(FIXTURES / "curriculum", [extra])
    assert any(
        e.startswith(f"{extra}/lexicon/a1.yaml:0:") and "duplicate id lex:tisch" in e
        for e in exc.value.errors
    )


def test_missing_extra_root_rejected() -> None:
    with pytest.raises(CurriculumError) as exc:
        load_curriculum(FIXTURES / "curriculum", [FIXTURES / "does-not-exist"])
    assert any("curriculum directory not found" in e for e in exc.value.errors)


def test_import_without_extra_root_suspends_only_extra_items(migrated_settings: Settings) -> None:
    with create_session_factory(migrated_settings)() as session:
        merged = load_curriculum(FIXTURES / "curriculum", [FIXTURES / "curriculum_extra"])
        report = import_curriculum(session, merged, datetime_now())
        session.commit()
    assert report.added == 19
    report = import_fixture(migrated_settings)
    assert (report.added, report.suspended, report.unchanged) == (0, 2, 17)
    with create_session_factory(migrated_settings)() as session:
        suspended = {i.id for i in session.scalars(select(Item)) if i.suspended}
    assert suspended == {"lex:sofa", "gram:private"}
