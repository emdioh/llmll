"""Versioned prompt files: `prompts/<task>.v<N>.md` with `## system` and `## user` sections."""

import re
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import NamedTuple

PROMPTS_DIR = Path(__file__).parent / "prompts"
# Inside the user section, text before this line is the stable (cacheable) part.
VARIABLE_MARKER = "<!-- variable -->"


class Prompt(NamedTuple):
    system: str
    user: Template
    version: str


def render_user(template: Template, values: dict[str, str]) -> tuple[str, str]:
    """Render a user section; returns `(stable, variable)` (stable is empty without a marker)."""
    text = template.substitute(values)
    stable, marker, variable = text.partition(VARIABLE_MARKER)
    if not marker:
        return "", text.strip()
    return stable.strip(), variable.strip()


def _split_sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## (system|user)\s*$", text, flags=re.MULTILINE)
    # parts = [preamble, name, body, name, body, ...]
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def latest_version(task: str, directory: Path = PROMPTS_DIR) -> int:
    versions = [
        int(m.group(1))
        for p in directory.glob(f"{task}.v*.md")
        if (m := re.fullmatch(rf"{re.escape(task)}\.v(\d+)\.md", p.name))
    ]
    if not versions:
        raise FileNotFoundError(f"no prompt file for task {task!r} in {directory}")
    return max(versions)


@lru_cache
def load_prompt(task: str, version: int | None = None) -> Prompt:
    """`(system, user_template, version)`; the highest version when `version` is omitted."""
    number = version if version is not None else latest_version(task)
    path = PROMPTS_DIR / f"{task}.v{number}.md"
    sections = _split_sections(path.read_text(encoding="utf-8"))
    if "system" not in sections or "user" not in sections:
        raise ValueError(f"{path.name}: needs `## system` and `## user` sections")
    return Prompt(sections["system"], Template(sections["user"]), f"v{number}")
