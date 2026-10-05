from app.domain.config import ReconcileConfig
from app.domain.reconcile import reconcile
from app.llm.types import GradeError, GradeResult
from app.nlp.types import LTMatch

CFG = ReconcileConfig()
TARGETS = ["gram:cases", "lex:tisch"]
CURRICULUM = {"gram:cases", "lex:tisch", "lex:katze", "lex:unknown-to-learner"}
KNOWN = {"lex:katze"}
ALLOWED = {"gram:cases": {"acc", "masc"}}


def err(
    start=0, end=3, item="gram:cases", tags=("acc",), severity="major", conf=0.7, original="die"
):
    return GradeError(
        start=start,
        end=end,
        original=original,
        correction="den",
        item_id=item,
        diagnostic_tags=list(tags),
        severity=severity,
        confidence=conf,
        explanation="perché",
    )


def grade(errors=(), overall="major_errors", correct_uses=()):
    return GradeResult(
        overall=overall,
        errors=list(errors),
        correct_uses=list(correct_uses),
        corrected_sentence="x",
        feedback="y",
    )


def lt(offset=0, length=3):
    return LTMatch(offset=offset, length=length, rule_id="R", category="GRAMMAR", message="m")


def run(g, matches=None, answer="die Tisch", **kw):
    return reconcile(
        g,
        matches,
        TARGETS,
        KNOWN,
        ALLOWED,
        CFG,
        curriculum_item_ids=CURRICULUM,
        answer=answer,
        **kw,
    )


def outcome(ev, item_id):
    return next(i for i in ev.items if i.item_id == item_id)


def test_unknown_item_id_is_dropped_but_error_kept() -> None:
    ev = run(grade([err(item="gram:nope")]), [])
    assert len(ev.errors) == 1 and ev.errors[0].item_id is None
    assert any("gram:nope" in n for n in ev.notes)


def test_tags_not_allowed_are_dropped() -> None:
    ev = run(grade([err(tags=("acc", "bogus"))]), [])
    assert ev.errors[0].diagnostic_tags == ["acc"]
    assert any("bogus" in n for n in ev.notes)
    assert outcome(ev, "gram:cases").diagnostic_tags == ("acc",)


def test_overlapping_lt_match_raises_confidence() -> None:
    ev = run(grade([err(conf=0.6)]), [lt(1, 2)])
    assert ev.errors[0].confidence == 0.9
    assert ev.unmatched_lt == ()
    # a higher LLM confidence is kept
    assert run(grade([err(conf=0.97)]), [lt()]).errors[0].confidence == 0.97


def test_major_without_lt_match_and_low_confidence_is_dampened() -> None:
    ev = run(grade([err(conf=0.7)]), [lt(offset=6, length=4)])
    assert abs(ev.errors[0].confidence - 0.56) < 1e-9
    assert [m.offset for m in ev.unmatched_lt] == [6]


def test_dampening_does_not_apply_to_confident_or_minor_errors() -> None:
    assert run(grade([err(conf=0.85)]), []).errors[0].confidence == 0.85
    assert (
        run(grade([err(conf=0.5, severity="minor")], "minor_errors"), []).errors[0].confidence
        == 0.5
    )


def test_lt_unavailable_means_no_adjustment() -> None:
    ev = run(grade([err(conf=0.7)]), None)
    assert ev.errors[0].confidence == 0.7
    assert ev.lt_available is False and ev.unmatched_lt == ()
    assert run(grade([err()]), []).lt_available is True


def test_unmatched_lt_lowers_correct_use_confidence() -> None:
    g = grade(overall="correct", correct_uses=["gram:cases"])
    assert outcome(run(g, [lt(5, 2)]), "gram:cases").confidence == 0.7
    assert outcome(run(g, []), "gram:cases").confidence == 1.0


def test_major_error_makes_item_error_with_min_confidence() -> None:
    ev = run(grade([err(conf=0.9), err(conf=0.9, severity="minor", tags=("masc",))]), [lt()])
    o = outcome(ev, "gram:cases")
    assert o.outcome == "error" and o.confidence == 0.9
    assert set(o.diagnostic_tags) == {"acc", "masc"}


def test_only_minor_errors_make_item_assisted() -> None:
    ev = run(grade([err(severity="minor", conf=0.9)], "minor_errors"), [lt()])
    assert outcome(ev, "gram:cases").outcome == "assisted"
    assert outcome(ev, "lex:tisch").outcome == "correct"  # target without errors


def test_correct_uses_and_targets_without_errors_are_correct() -> None:
    ev = run(grade(overall="correct", correct_uses=["lex:katze"]), [])
    assert {i.item_id: i.outcome for i in ev.items} == {
        "gram:cases": "correct",
        "lex:tisch": "correct",
        "lex:katze": "correct",
    }


def test_target_not_evidenced_when_major_errors_elsewhere() -> None:
    ev = run(grade([err(item="lex:katze", tags=())], "major_errors"), [])
    ids = {i.item_id for i in ev.items}
    assert ids == {"lex:katze"}  # targets without errors or correct use: no evidence
    assert outcome(ev, "lex:katze").outcome == "error"


def test_items_unknown_to_the_learner_are_ignored_unless_targets() -> None:
    ev = run(
        grade(
            [err(item="lex:unknown-to-learner", tags=())], correct_uses=["lex:unknown-to-learner"]
        ),
        [],
    )
    assert "lex:unknown-to-learner" not in {i.item_id for i in ev.items}
    assert len(ev.errors) == 1  # the error itself is kept


def test_off_task_marks_targets_uncertain_errors() -> None:
    ev = run(grade(overall="off_task"), [])
    assert {(i.item_id, i.outcome, i.confidence) for i in ev.items} == {
        ("gram:cases", "error", 0.5),
        ("lex:tisch", "error", 0.5),
    }


def test_used_hint_turns_correct_into_assisted() -> None:
    ev = run(grade(overall="correct", correct_uses=["lex:katze"]), [], used_hint=True)
    assert {i.outcome for i in ev.items} == {"assisted"}


def test_spans_are_repaired_from_original_text() -> None:
    ev = run(grade([err(start=40, end=43, original="Tisch")]), [], answer="die Tisch")
    assert (ev.errors[0].start, ev.errors[0].end) == (4, 9)
    assert any("repaired" in n for n in ev.notes)


def test_evaluation_to_dict_is_json_ready() -> None:
    import json

    ev = run(grade([err()]), [lt()])
    data = ev.to_dict()
    assert json.loads(json.dumps(data))["reconcile_version"] == CFG.version
