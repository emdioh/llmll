"""Turn typed requests into the text values of the prompt templates."""

from app.llm.types import (
    ErrorContext,
    ExerciseRequest,
    ExplainRequest,
    GlossRequest,
    GradeRequest,
    ItemContext,
    SimplifyRequest,
    TargetContext,
    VocabEntry,
)
from app.nlp.types import LTMatch

LANGUAGE_NAMES = {"it": "Italian (it)", "en": "English (en)", "de": "German (de)"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def neutralize(text: str) -> str:
    """Make untrusted text safe inside tags without changing character offsets."""
    return text.replace("<", "‹")


def render_item(item: ItemContext) -> str:
    head = f"[{item.item_id}] {item.kind}, level {item.level}: {item.label}"
    lines = [head]
    if item.kind == "lemma":
        details = []
        if item.pos:
            details.append(f"pos {item.pos}")
        if item.plural:
            details.append(f"plural {item.plural}")
        if item.translation_it:
            details.append(f"it: {item.translation_it}")
        if item.translation_en:
            details.append(f"en: {item.translation_en}")
        lines.append("  " + "; ".join(details))
    elif item.kind == "construction":
        if item.pattern:
            lines.append(f"  pattern: {item.pattern}")
        if item.translation_it:
            lines.append(f"  it: {item.translation_it}")
    if item.example:
        lines.append(f"  example: {item.example.de} = {item.example.it}")
    if item.diagnostic_tags:
        tags = "; ".join(f"{dim}: {', '.join(vals)}" for dim, vals in item.diagnostic_tags.items())
        lines.append(f"  diagnostic tags: {tags}")
    if item.reference_it:
        lines.append("  curated reference text:")
        lines.append("  <reference>")
        lines.extend("  " + line for line in item.reference_it.strip().splitlines())
        lines.append("  </reference>")
    return "\n".join(lines)


def render_targets(targets: list[TargetContext]) -> str:
    return "\n\n".join(render_item(t.item) for t in targets) or "(none)"


def render_roles(targets: list[TargetContext]) -> str:
    lines = []
    for t in targets:
        parts = [f"{t.item.item_id}: {t.role}, weight {t.weight}"]
        if t.is_new:
            parts.append("NEW for the learner (must be in the glossary)")
        if t.focus_tags:
            parts.append(f"focus tags: {', '.join(t.focus_tags)}")
        lines.append("; ".join(parts))
    return "\n".join(lines) or "(none)"


def render_vocab(vocab: list[VocabEntry]) -> str:
    return "\n".join(f"{v.item_id}: {v.de} = {v.translation}" for v in vocab) or "(none)"


def render_lt(matches: list[LTMatch] | None, answer: str) -> str:
    if matches is None:
        return "(LanguageTool was not available)"
    if not matches:
        return "(no matches)"
    lines = []
    for m in matches:
        span = neutralize(answer[m.offset : m.offset + m.length])
        repl = f" -> {', '.join(m.replacements)}" if m.replacements else ""
        lines.append(
            f"offset {m.offset}, length {m.length}, rule {m.rule_id} ({m.category}): "
            f"{m.message} [text: {span!r}]{repl}"
        )
    return "\n".join(lines)


def render_error(error: ErrorContext | None) -> str:
    if error is None:
        return "(none: on-demand question)"
    tags = ", ".join(error.diagnostic_tags) or "(none)"
    return (
        f"learner answer: {neutralize(error.answer)}\n"
        f"wrong part: {neutralize(error.original)}\n"
        f"correction: {error.correction}\n"
        f"diagnostic tags: {tags}"
    )


def exercise_vars(req: ExerciseRequest) -> dict[str, str]:
    return {
        "level": req.level,
        "language": language_name(req.explanation_language),
        "target_items": render_targets(req.targets),
        "exercise_type": req.exercise_type,
        "target_roles": render_roles(req.targets),
        "known_vocabulary": render_vocab(req.known_vocabulary),
    }


def grade_vars(req: GradeRequest) -> dict[str, str]:
    allowed = "\n".join(
        f"{i}: {', '.join(tags) or '(none)'}" for i, tags in req.allowed_tags.items()
    )
    return {
        "level": req.level,
        "language": language_name(req.explanation_language),
        "target_items": render_targets(req.targets),
        "allowed_tags": allowed or "(none)",
        "exercise_type": req.exercise_type,
        "instructions": req.instructions,
        "prompt": req.prompt,
        "glossary": "\n".join(f"{g.item_id}: {g.de} = {g.translation}" for g in req.glossary)
        or "(none)",
        "reference_solutions": "\n".join(f"- {s}" for s in req.reference_solutions),
        "lt_matches": render_lt(req.lt_matches, req.answer),
        "answer": neutralize(req.answer),
    }


def explain_vars(req: ExplainRequest) -> dict[str, str]:
    known = "\n".join(f"{g.item_id}: {g.title}" for g in req.known_grammar)
    return {
        "level": req.level,
        "language": language_name(req.explanation_language),
        "item": render_item(req.item),
        "known_grammar": known or "(none)",
        "error": render_error(req.error),
        "question": neutralize(req.question or "(none)"),
    }


def _csv(values: list[str]) -> str:
    return ", ".join(values) or "(none)"


def simplify_vars(req: SimplifyRequest) -> dict[str, str]:
    if req.mode == "generate":
        task = (
            "Write a new text from scratch on the topic below (free choice if none is given), "
            "built around the seed words."
        )
    else:
        task = "Rewrite the source text for the learner."
    replace = "\n".join(
        f"- {w.lemma}" + (f" (forms in the text: {', '.join(w.forms)})" if w.forms else "")
        for w in req.replace_words
    )
    if req.previous_text:
        retry = (
            "This is a revision. The previous attempt below still contains words outside the "
            "learner's vocabulary. Rewrite it so that none of the words listed here remain "
            "(use simpler words or rephrase), keeping the content.\n"
            f"<words_to_replace>\n{replace or '(none)'}\n</words_to_replace>\n"
            f"<previous_attempt>\n{neutralize(req.previous_text)}\n</previous_attempt>"
        )
    else:
        retry = "(first attempt)"
    return {
        "level": req.level,
        "language": language_name(req.explanation_language),
        "allowed_vocabulary": _csv(req.allowed_lemmas),
        "candidate_vocabulary": _csv(req.candidate_lemmas),
        "known_grammar": _csv(req.known_grammar),
        "task": task,
        "source_language": "German" if req.source_language == "de" else "not German (translate)",
        "max_words": str(req.max_words),
        "topic": neutralize(req.topic or "(none)"),
        "seed_words": _csv(req.seed_lemmas),
        "source_text": neutralize(req.source_text) or "(none)",
        "retry": retry,
    }


def gloss_vars(req: GlossRequest) -> dict[str, str]:
    return {
        "level": req.level,
        "language": language_name(req.explanation_language),
        "word": neutralize(req.word),
        "lemma": neutralize(req.lemma),
        "sentence": neutralize(req.sentence),
    }
