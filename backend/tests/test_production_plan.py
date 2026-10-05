from app.domain.production import (
    NEW_LEMMA_WEIGHT,
    PRIMARY_WEIGHT,
    SECONDARY_WEIGHT,
    Candidate,
    plan_production,
)


def g(item_id: str, mastery: float | None = 0.8, r: float | None = None, kind: str = "grammar"):
    return Candidate(item_id, kind, mastery, r)


def lem(item_id: str) -> Candidate:
    return Candidate(item_id, "lemma")


def plan(slots=2, remediation=(), due=(), new_grammar=(), new_lemmas=(), gb=2, lb=5):
    return plan_production(slots, remediation, due, new_grammar, new_lemmas, gb, lb)


def primaries(exercises):
    return [e.targets[0].item_id for e in exercises]


def test_priority_remediation_then_due_then_new() -> None:
    result = plan(
        slots=3,
        remediation=[g("gram:r")],
        due=[g("gram:d1", r=0.9), g("gram:d2", r=0.5)],
        new_grammar=[g("gram:n")],
    )
    assert primaries(result) == ["gram:r", "gram:d2", "gram:d1"]  # lowest retrievability first
    four = plan(
        slots=4, remediation=[g("gram:r")], due=[g("gram:d", r=0.4)], new_grammar=[g("gram:n")]
    )
    assert primaries(four) == ["gram:r", "gram:d", "gram:n"]


def test_at_most_one_new_grammar_per_exercise_and_budget_respected() -> None:
    new = [g("gram:n1", None), g("gram:n2", None), g("gram:n3", None)]
    result = plan(slots=3, new_grammar=new, gb=2)
    assert primaries(result) == ["gram:n1", "gram:n2"]  # budget 2: third slot is skipped
    for ex in result:
        assert sum(t.new and t.kind != "lemma" for t in ex.targets) == 1
    assert plan(slots=2, new_grammar=new, gb=0) == []


def test_new_lemmas_budget_cap_per_exercise_and_weights() -> None:
    result = plan(
        slots=2,
        new_grammar=[g("gram:n", None)],
        new_lemmas=[lem(f"lex:{i}") for i in range(6)],
        lb=5,
    )
    first = result[0]
    assert first.targets[0].weight == PRIMARY_WEIGHT and first.targets[0].new
    secondary = first.targets[1:]
    assert len(secondary) == 3 and all(t.weight == NEW_LEMMA_WEIGHT and t.new for t in secondary)
    # the second exercise has no grammar primary: a lemma becomes primary; budget 5 total
    total_new = sum(t.new and t.kind == "lemma" for ex in result for t in ex.targets)
    assert total_new == 5
    assert result[1].targets[0].kind == "lemma"


def test_remediation_lemmas_are_secondary_with_half_weight() -> None:
    result = plan(slots=1, remediation=[g("gram:r"), lem("lex:r")], new_lemmas=[lem("lex:n")])
    targets = result[0].targets
    assert [t.item_id for t in targets] == ["gram:r", "lex:r", "lex:n"]
    assert targets[1].weight == SECONDARY_WEIGHT and not targets[1].new
    assert targets[2].weight == NEW_LEMMA_WEIGHT and targets[2].new


def test_secondary_targets_capped_at_three() -> None:
    result = plan(
        slots=1,
        remediation=[g("gram:r"), *[lem(f"lex:r{i}") for i in range(4)]],
        new_lemmas=[lem("lex:n")],
    )
    assert len(result[0].targets) == 4  # 1 primary + 3 secondary


def test_type_selection() -> None:
    assert plan(slots=1, new_grammar=[g("gram:n", None)])[0].subtype == "translation"
    assert plan(slots=1, due=[g("gram:weak", 0.3, 0.5)])[0].subtype == "translation"
    strong = plan(slots=3, due=[g(f"gram:s{i}", 0.9, 0.5 + i / 10) for i in range(3)])
    assert [e.subtype for e in strong] == ["guided", "transform", "translation"]


def test_nothing_to_practise_yields_no_exercise() -> None:
    assert plan(slots=2) == []
    assert plan(slots=0, new_grammar=[g("gram:n", None)]) == []


def test_no_item_is_used_twice() -> None:
    result = plan(slots=2, remediation=[g("gram:a")], due=[g("gram:a", r=0.1), g("gram:b", r=0.2)])
    ids = [t.item_id for ex in result for t in ex.targets]
    assert len(ids) == len(set(ids))
