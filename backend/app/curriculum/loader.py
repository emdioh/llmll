"""Load and validate a curriculum directory. All errors are reported at once."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from app.curriculum.schema import ConstructionEntry, GrammarEntry, LexiconEntry

NON_PAYLOAD_FIELDS = {"id", "level", "interference", "requires"}


class CurriculumError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class LoadedItem:
    id: str
    kind: str
    level: str
    payload: dict[str, Any]
    interference: dict[str, Any]
    requires: list[str]
    source_file: str
    index: int
    content_hash: str


@dataclass
class Curriculum:
    items: list[LoadedItem] = field(default_factory=list)


def _to_item(model: BaseModel, kind: str, source_file: str, index: int) -> LoadedItem:
    data = model.model_dump(mode="json", exclude_none=True)
    payload = {k: v for k, v in data.items() if k not in NON_PAYLOAD_FIELDS}
    interference = data.get("interference", {})
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return LoadedItem(
        id=data["id"],
        kind=kind,
        level=data["level"],
        payload=payload,
        interference=interference,
        requires=list(data.get("requires", [])),
        source_file=source_file,
        index=index,
        content_hash=hashlib.sha256(blob.encode()).hexdigest(),
    )


def _format_validation_error(prefix: str, exc: ValidationError) -> list[str]:
    errors = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        errors.append(f"{prefix}: {loc + ': ' if loc else ''}{err['msg']}")
    return errors


def _read_yaml(path: Path, errors: list[str], rel: str) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"{rel}:0: invalid YAML: {exc}")
    return None


def _load_list(
    path: Path,
    rel: str,
    model: type[BaseModel],
    kind: str,
    items: list[LoadedItem],
    errors: list[str],
) -> None:
    data = _read_yaml(path, errors, rel)
    if data is None:
        if not errors or not errors[-1].startswith(f"{rel}:"):
            errors.append(f"{rel}:0: file is empty")
        return
    if not isinstance(data, list):
        errors.append(f"{rel}:0: expected a YAML list")
        return
    for index, raw in enumerate(data):
        try:
            items.append(_to_item(model.model_validate(raw), kind, rel, index))
        except ValidationError as exc:
            errors.extend(_format_validation_error(f"{rel}:{index}", exc))


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                return [*stack[stack.index(nxt) :], nxt]
            if state.get(nxt) is None and nxt in graph:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in graph:
        if state.get(node) is None:
            found = visit(node)
            if found:
                return found
    return None


def _validate_graph(items: list[LoadedItem], errors: list[str]) -> None:
    seen: dict[str, LoadedItem] = {}
    for item in items:
        if item.id in seen:
            first = seen[item.id]
            errors.append(
                f"{item.source_file}:{item.index}: duplicate id {item.id} "
                f"(first defined at {first.source_file}:{first.index})"
            )
        else:
            seen[item.id] = item
    graph: dict[str, list[str]] = {}
    for item in items:
        for req in item.requires:
            if req not in seen:
                errors.append(f"{item.source_file}:{item.index}: unknown prerequisite {req}")
        graph.setdefault(item.id, []).extend(r for r in item.requires if r in seen)
    cycle = _find_cycle(graph)
    if cycle:
        errors.append("prerequisite cycle: " + " -> ".join(cycle))


def load_curriculum(root: Path) -> Curriculum:
    """Load `root` (e.g. `curriculum/de`); raise `CurriculumError` listing every problem."""
    errors: list[str] = []
    items: list[LoadedItem] = []
    if not root.is_dir():
        raise CurriculumError([f"{root}: curriculum directory not found"])

    for path in sorted((root / "lexicon").glob("*.yaml")):
        _load_list(path, f"lexicon/{path.name}", LexiconEntry, "lemma", items, errors)

    for path in sorted((root / "grammar").glob("*.yaml")):
        rel = f"grammar/{path.name}"
        data = _read_yaml(path, errors, rel)
        if data is None:
            continue
        try:
            entry = GrammarEntry.model_validate(data)
        except ValidationError as exc:
            errors.extend(_format_validation_error(f"{rel}:0", exc))
            continue
        if entry.id != f"gram:{path.stem}":
            errors.append(f"{rel}:0: id {entry.id} does not match file name (gram:{path.stem})")
        items.append(_to_item(entry, "grammar", rel, 0))

    constructions = root / "constructions.yaml"
    if constructions.is_file():
        _load_list(
            constructions, "constructions.yaml", ConstructionEntry, "construction", items, errors
        )

    _validate_graph(items, errors)
    if errors:
        raise CurriculumError(errors)
    return Curriculum(items=items)
