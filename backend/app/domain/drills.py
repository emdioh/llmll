"""Planning of a grammar drill: several exercises on one point, easiest first. Pure."""

from dataclasses import dataclass

# Open exercises, in the order they are added after the closed ones.
OPEN_SUBTYPES = ("translation", "transform", "guided")


@dataclass(frozen=True)
class DrillStep:
    subtype: str  # choice | cloze | translation | transform | guided
    focus_tags: tuple[str, ...]


def plan_drill(size: int, tags: list[str]) -> list[DrillStep]:
    """`size` steps: about a third multiple choice, then cloze, then open exercises.

    Each step focuses on the next diagnostic tag of the point (its sub-cases) in turn, so that
    the drill covers them rather than repeating one.
    """
    size = max(size, 1)
    choice = max(1, round(size / 3)) if size > 1 else 0
    open_ = max(1, size // 3) if size > 2 else 0
    cloze = size - choice - open_
    subtypes = (
        ["choice"] * choice
        + ["cloze"] * cloze
        + [OPEN_SUBTYPES[i % len(OPEN_SUBTYPES)] for i in range(open_)]
    )
    return [
        DrillStep(subtype, (tags[i % len(tags)],) if tags else ())
        for i, subtype in enumerate(subtypes)
    ]
