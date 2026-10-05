"""New-item budget (R§7.4)."""

from dataclasses import dataclass

from app.domain.config import LearnerSettings


@dataclass(frozen=True)
class NewItemBudget:
    lemmas: int
    grammar: int


def new_item_budget(
    introduced_lemmas_7d: int,
    introduced_grammar_7d: int,
    backlog: int,
    settings: LearnerSettings,
) -> NewItemBudget:
    lemmas = max(settings.weekly_new_lemmas - introduced_lemmas_7d, 0)
    grammar = max(settings.weekly_new_grammar - introduced_grammar_7d, 0)
    if backlog > 2 * settings.review_cap:
        return NewItemBudget(0, 0)
    if backlog > settings.review_cap:
        return NewItemBudget(lemmas // 2, grammar // 2)
    return NewItemBudget(lemmas, grammar)
