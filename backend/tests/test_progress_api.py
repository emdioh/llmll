# ruff: noqa: F811
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.store.db import create_session_factory
from app.store.models import (
    Attempt,
    Exercise,
    ItemMemory,
    Learner,
    LearnerItem,
    LearningEvent,
    RemediationItem,
)

from .conftest import Clock
from .progress_helpers import TZ, history_api, llm, post_answer, seed_history  # noqa: F401
from .test_api_production import lt  # noqa: F401

P = "/api/progress"


def get(api: TestClient, path: str, **params):
    resp = api.get(f"{P}/{path}", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def db_of(settings: Settings):
    return create_session_factory(settings)()


@pytest.fixture
def seeded(history_api: TestClient, migrated_settings: Settings, clock: Clock) -> dict:
    return seed_history(history_api, migrated_settings, clock)


# --- the timezone setting ------------------------------------------------------------------------


def test_timezone_setting(history_api: TestClient) -> None:
    assert history_api.post("/api/learner", json={"level": "A1"}).json()["settings"][
        "timezone"
    ] == ("UTC")
    assert history_api.get("/api/settings").json()["timezone"] == "UTC"
    for bad in ("Mars/Olympus", "", "../etc/passwd", "europe/NOPE"):
        assert history_api.put("/api/settings", json={"timezone": bad}).status_code == 422, bad
    assert history_api.get("/api/settings").json()["timezone"] == "UTC"
    resp = history_api.put("/api/settings", json={"timezone": TZ})
    assert resp.status_code == 200 and resp.json()["timezone"] == TZ
    assert history_api.get("/api/learner").json()["settings"]["timezone"] == TZ
    # Other settings keep working and keep the zone.
    assert history_api.put("/api/settings", json={"review_cap": 9}).json()["timezone"] == TZ
    assert history_api.get(f"{P}/summary").json()["timezone"] == TZ


def test_learners_stored_before_the_setting_default_to_utc(
    history_api: TestClient, migrated_settings: Settings
) -> None:
    history_api.post("/api/learner", json={"level": "A1"})
    with db_of(migrated_settings) as db:
        learner = db.get(Learner, 1)
        learner.settings = {k: v for k, v in learner.settings.items() if k != "timezone"}
        db.commit()
    assert history_api.get("/api/settings").json()["timezone"] == "UTC"
    assert history_api.get(f"{P}/summary").json()["timezone"] == "UTC"


def test_the_time_zone_decides_which_day_an_answer_belongs_to(
    history_api: TestClient, clock: Clock
) -> None:
    history_api.post("/api/learner", json={"level": "A1"})
    history_api.put("/api/settings", json={"production_slots": 0})
    clock.advance(hours=13, minutes=30)  # 22:30 UTC on 5 October = 00:30 on the 6th in Rome
    session = history_api.post("/api/sessions").json()
    post_answer(history_api, session["session_id"], session["cards"][0])

    assert get(history_api, "summary")["streak"]["last_study_day"] == "2026-10-05"
    history_api.put("/api/settings", json={"timezone": TZ})
    summary = get(history_api, "summary")
    assert summary["streak"]["last_study_day"] == "2026-10-06"
    assert summary["today"] == "2026-10-06" and summary["streak"]["studied_today"] is True
    days = {d["date"]: d for d in get(history_api, "activity", days=3)["days"]}
    assert days["2026-10-06"]["active"] and not days["2026-10-05"]["active"]


# --- nothing yet ---------------------------------------------------------------------------------


def test_progress_needs_a_learner(history_api: TestClient) -> None:
    for path in ("summary", "activity", "levels", "forecast", "items", "history"):
        assert history_api.get(f"{P}/{path}").status_code == 404
    assert history_api.post(f"{P}/items/lex:tisch/practice").status_code == 404


def test_progress_of_a_new_learner_is_empty(history_api: TestClient) -> None:
    history_api.post("/api/learner", json={"level": "A2"})
    summary = get(history_api, "summary")
    assert summary["streak"] == {
        "current": 0,
        "longest": 0,
        "last_study_day": None,
        "studied_today": False,
    }
    assert summary["study_days_total"] == 0
    assert summary["totals"] == {
        "reviews": 0,
        "exercises": 0,
        "readings": 0,
        "items_introduced": 0,
        "minutes": 0.0,
    }
    assert summary["retention"]["observed"] is None and summary["due_now"] == 0
    assert summary["states"]["mature"] == 0 and summary["states"]["new"] > 0
    assert get(history_api, "history") == {"total": 0, "limit": 20, "offset": 0, "items": []}
    assert get(history_api, "items")["items"] == []
    # Items never met have no memory but still a detail: new, or presumed known by level.
    unseen = get(history_api, "items/gram:cases")
    assert unseen["facets"] == [] and unseen["state"] == "new" and unseen["mastery"] is None
    assert unseen["recent_answers"] == [] and unseen["explanation"] is None
    assert get(history_api, "items/lex:tisch")["state"] == "presumed_known"
    assert all(d["active"] is False for d in get(history_api, "activity", days=10)["days"])
    assert [f["due"] for f in get(history_api, "forecast", days=3)] == [0, 0, 0]


# --- summary, activity, levels, forecast ---------------------------------------------------------


def test_summary_streaks_and_placement(
    history_api: TestClient, migrated_settings: Settings, seeded: dict
) -> None:
    summary = get(history_api, "summary")
    assert summary["timezone"] == TZ and summary["today"] == "2026-10-10"
    # Studied on 6, 7 and 9 October; the placement on the 5th is not study; today is still open.
    assert summary["streak"] == {
        "current": 1,
        "longest": 2,
        "last_study_day": "2026-10-09",
        "studied_today": False,
    }
    assert summary["study_days_total"] == 3

    with db_of(migrated_settings) as db:
        rows = db.execute(
            select(Exercise.type, Exercise.session_id).join(
                Attempt, Attempt.exercise_id == Exercise.id
            )
        ).all()
        placement_attempts = [r for r in rows if r.session_id.startswith("placement-")]
    assert len(placement_attempts) == 3
    totals = summary["totals"]
    counted = [r for r in rows if not r.session_id.startswith("placement-")]
    assert totals["reviews"] == sum(
        r.type.startswith("flashcard_r") or r.type == "flashcard_production" for r in counted
    )
    assert totals["exercises"] == sum(r.type == "production" for r in counted)
    assert totals["readings"] == 1 and totals["items_introduced"] > 0
    # 20 s + 30 s + 3 s of recorded time on the 6th, 10 minutes of reading on the 9th.
    assert totals["minutes"] == pytest.approx(0.9 + 10.0, abs=0.11)

    assert summary["this_week"]["study_days"] == 3 and summary["last_week"]["study_days"] == 0
    assert summary["this_week"]["readings"] == 1
    assert summary["this_week"]["exercises"] == totals["exercises"]
    states = summary["states"]
    assert sum(states.values()) == sum(summary["states_words"].values()) + sum(
        summary["states_grammar"].values()
    )
    assert states["young"] + states["mature"] + states["learning"] > 0
    # The placement answers count for item state: the three items are not "new".
    for item_id in seeded["placement_items"]:
        assert get(history_api, f"items/{item_id}")["state"] != "new"
    retention = summary["retention"]
    assert retention["target"] == 0.85 and retention["n_reviews"] > 0
    assert summary["due_now"] == get(history_api, "forecast", days=1)[0]["due"]
    assert summary["due_today"] >= summary["due_now"]

    # Studying today extends the streak.
    clock_day = history_api.app.state.now
    clock_day.advance(minutes=5)
    session = history_api.post("/api/sessions").json()
    card = next(c for c in session["cards"] if c["type"] != "production")
    post_answer(history_api, session["session_id"], card)
    after = get(history_api, "summary")
    assert after["streak"]["current"] == 2 and after["streak"]["studied_today"] is True
    assert after["study_days_total"] == 4


def test_activity_days_and_weekly_accuracy(history_api: TestClient, seeded: dict) -> None:
    body = get(history_api, "activity", days=7)
    days = {d["date"]: d for d in body["days"]}
    assert list(days) == [f"2026-10-{n:02d}" for n in range(4, 11)]
    assert [days[d]["active"] for d in days] == [False, False, True, True, False, True, False]
    # The placement day shows no activity at all.
    assert days["2026-10-05"]["reviews"] == 0 and days["2026-10-05"]["exercises"] == 0
    assert days["2026-10-06"]["exercises"] == 2 and days["2026-10-06"]["minutes"] == 0.9
    assert days["2026-10-09"]["readings"] == 1 and days["2026-10-09"]["minutes"] >= 10.0
    assert days["2026-10-06"]["new_items"] > 0

    weeks = {w["week_start"]: w for w in body["weeks"]}
    assert list(weeks) == ["2026-09-28", "2026-10-05"]
    assert weeks["2026-09-28"]["n"] == 0 and weeks["2026-09-28"]["production_correct_rate"] is None
    week = weeks["2026-10-05"]
    assert week["production_n"] > 0 and 0 < week["production_correct_rate"] < 1
    assert week["n"] == week["flashcards_n"] + week["production_n"]

    long = get(history_api, "activity")
    assert len(long["days"]) == 140 and long["days"][-1]["date"] == "2026-10-10"
    assert history_api.get(f"{P}/activity", params={"days": 0}).status_code == 422
    assert history_api.get(f"{P}/activity", params={"days": 400}).status_code == 422


def test_levels(history_api: TestClient, seeded: dict) -> None:
    rows = get(history_api, "levels")
    keys = [(r["level"], r["kind"]) for r in rows]
    assert keys == sorted(keys, key=lambda k: (k[0], k[1]))
    summary = get(history_api, "summary")
    for row in rows:
        assert (
            row["new"] + row["learning"] + row["young"] + row["mature"] + row["presumed_known"]
            == row["total"]
        )
    assert sum(r["total"] for r in rows) == sum(summary["states"].values())
    a1_words = next(r for r in rows if (r["level"], r["kind"]) == ("A1", "lemma"))
    assert a1_words["total"] == 10 and a1_words["introduced"] > 0
    assert a1_words["mean_mastery"] is not None
    assert (
        next(r for r in rows if (r["level"], r["kind"]) == ("A1", "grammar"))["presumed_known"] == 1
    )


def test_forecast(history_api: TestClient, migrated_settings: Settings, seeded: dict) -> None:
    forecast = get(history_api, "forecast", days=14)
    assert [f["date"] for f in forecast][:2] == ["2026-10-10", "2026-10-11"]
    assert len(forecast) == 14
    with db_of(migrated_settings) as db:
        due = [d for (d,) in db.execute(select(ItemMemory.due)) if d is not None]
    assert sum(f["due"] for f in forecast) == len(due) - sum(
        1 for d in due if d.date() > date(2026, 10, 23)
    )
    assert forecast[0]["due"] == sum(1 for d in due if d.date() <= date(2026, 10, 10))
    assert len(get(history_api, "forecast", days=3)) == 3
    assert history_api.get(f"{P}/forecast", params={"days": 0}).status_code == 422
    assert history_api.get(f"{P}/forecast", params={"days": 61}).status_code == 422


# --- items ---------------------------------------------------------------------------------------


def test_item_lists_and_sorts(history_api: TestClient, seeded: dict) -> None:
    everything = get(history_api, "items", sort="recent", limit=200)
    ids = [i["id"] for i in everything["items"]]
    assert everything["total"] == len(ids) and "gram:cases" in ids
    stamps = [i["last_practiced"] for i in everything["items"]]
    assert stamps == sorted(stamps, reverse=True)

    # weakest: lowest mastery first, only items with enough evidence (n_eff >= 2) when any.
    weakest = get(history_api, "items", sort="weakest")["items"]
    assert [i["id"] for i in weakest] == ["gram:cases"]
    assert weakest[0]["n_eff"] >= 2 and weakest[0]["errors"] == 2 and weakest[0]["answers"] == 3
    assert weakest[0]["error_rate"] == pytest.approx(2 / 3)
    assert weakest[0]["top_tags"] == [{"tag": "m", "count": 2}]
    assert weakest[0]["kind"] == "grammar" and weakest[0]["label"] == "Casi"
    assert [f["facet"] for f in weakest[0]["facets"]] == ["production"]
    strongest = get(history_api, "items", sort="strongest", kind="lemma", limit=200)["items"]
    masteries = [i["mastery"] for i in strongest]
    assert masteries == sorted(masteries, reverse=True)

    # Without enough evidence anywhere, the weakest list falls back to every item with memory.
    words = get(history_api, "items", sort="weakest", kind="lemma", limit=200)["items"]
    assert words and [i["mastery"] for i in words] == sorted(i["mastery"] for i in words)

    errors = get(history_api, "items", sort="errors")["items"]
    # The wrong placement answer counts for item state, so it shows up here too.
    assert [(i["id"], i["errors"]) for i in errors] == [("gram:cases", 2), ("lex:gehen", 1)]
    due = get(history_api, "items", sort="due", limit=200)["items"]
    dues = [i["due"] for i in due]
    assert dues == sorted(dues) and len(dues) == len(due) > 0

    # Filters, search and pagination.
    grammar = get(history_api, "items", kind="grammar", sort="recent")
    assert {i["kind"] for i in grammar["items"]} == {"grammar"}
    a2 = get(history_api, "items", level="A2", sort="recent", limit=200)["items"]
    assert a2 and {i["level"] for i in a2} == {"A2"}
    found = get(history_api, "items", q="tisch", sort="recent", limit=200)["items"]
    assert [i["id"] for i in found] == ["lex:tisch"]
    young = get(history_api, "items", state="young", sort="recent", limit=200)["items"]
    assert young and {i["state"] for i in young} == {"young"}
    page = get(history_api, "items", sort="recent", limit=2, offset=1)
    assert [i["id"] for i in page["items"]] == ids[1:3] and page["total"] == len(ids)
    assert history_api.get(f"{P}/items", params={"sort": "luckiest"}).status_code == 422
    assert history_api.get(f"{P}/items", params={"limit": 500}).status_code == 422


def test_item_detail(history_api: TestClient, migrated_settings: Settings, seeded: dict) -> None:
    detail = get(history_api, "items/gram:cases")
    assert detail["item"]["id"] == "gram:cases" and detail["item"]["label"] == "Casi"
    assert detail["state"] == "young" and detail["counts"] == {
        "correct": 1,
        "assisted": 0,
        "error": 2,
    }
    assert detail["tag_errors"] == [{"tag": "m", "count": 2}]
    (facet,) = detail["facets"]
    assert facet["facet"] == "production" and facet["counts"]["error"] == 2
    assert facet["tag_errors"] == [{"tag": "m", "count": 2}]

    # The trajectory is the replay of the item's events; it ends in the stored state.
    trajectory = facet["trajectory"]
    assert [p["kind"] for p in trajectory] == ["introduce", "review", "review", "review"]
    assert [p["outcome"] for p in trajectory] == [None, "error", "error", "correct"]
    assert trajectory[0]["mastery"] == 0.5 and trajectory[1]["mastery"] < 0.5
    with db_of(migrated_settings) as db:
        row = db.get(ItemMemory, (1, "gram:cases", "production"))
        assert trajectory[-1]["mastery"] == row.mastery
        assert trajectory[-1]["stability"] == row.stability
        assert facet["mastery"] == row.mastery and facet["stability"] == row.stability
        assert trajectory[-1]["event_id"] == row.last_event_id or row.last_event_id > 0
    assert facet["trajectory_total"] == 4

    # Recent answers: newest first, with prompt, answer, correction and the errors on the item.
    answers = detail["recent_answers"]
    assert [a["outcome"] for a in answers] == ["correct", "error", "error"]
    newest_first = [a["answered_at"] for a in answers]
    assert newest_first == sorted(newest_first, reverse=True)
    wrong = answers[-1]
    assert wrong["answer"] == "Das Tisch" and wrong["expected"] == "Der Tisch."
    assert wrong["prompt"] == "Il tavolo." and wrong["item_outcome"] == "error"
    (error,) = wrong["item_errors"]
    assert (error["start"], error["end"], error["original"]) == (0, 9, "Das Tisch")
    assert error["correction"] == "Der Tisch." and error["diagnostic_tags"] == ["m"]
    assert error["label"] == "Casi"
    assert wrong["contest"] is None and wrong["feedback"]
    assert answers[0]["answer"] and answers[0]["item_errors"] == []

    assert detail["explanation"] is None and detail["practice_queued"] is False
    explain = history_api.post(
        f"/api/evaluations/{seeded['wrong_evaluation']}/explain", json={"item_id": "gram:cases"}
    )
    assert explain.status_code == 200
    cached = get(history_api, "items/gram:cases")["explanation"]
    assert cached["markdown"] == explain.json()["markdown"]
    assert cached["evaluation_id"] == seeded["wrong_evaluation"]


def test_lemma_detail_has_both_facets_and_lookups(history_api: TestClient, seeded: dict) -> None:
    detail = get(history_api, "items/lex:tisch")
    assert detail["item"]["kind"] == "lemma"
    facets = {f["facet"]: f for f in detail["facets"]}
    assert set(facets) == {"recognition", "production"}
    kinds = {p["kind"] for p in facets["recognition"]["trajectory"]}
    assert "lookup" in kinds  # the reading looked the word up
    assert detail["mastery"] is not None
    assert history_api.get(f"{P}/items/nope").status_code == 404


def test_every_trajectory_ends_in_the_stored_memory(
    history_api: TestClient, migrated_settings: Settings, seeded: dict
) -> None:
    with db_of(migrated_settings) as db:
        rows = [
            (r.item_id, r.facet, r.mastery, r.stability, r.n_effective)
            for r in db.scalars(select(ItemMemory))
        ]
    assert len(rows) > 10
    for item_id, facet, mastery, stability, n_eff in rows:
        detail = get(history_api, f"items/{item_id}")
        entry = next(f for f in detail["facets"] if f["facet"] == facet)
        last = entry["trajectory"][-1]
        assert last["mastery"] == mastery and last["stability"] == stability, (item_id, facet)
        assert entry["n_eff"] == n_eff


def test_practice_queues_the_item_for_the_next_session(
    history_api: TestClient, migrated_settings: Settings, seeded: dict
) -> None:
    with db_of(migrated_settings) as db:
        before = db.scalar(select(func.count()).select_from(LearningEvent))
        memory_before = {
            (m.item_id, m.facet): (m.mastery, m.last_event_id)
            for m in db.scalars(select(ItemMemory))
        }
    # Items the learner has not met cannot be practiced; unknown items do not exist.
    with db_of(migrated_settings) as db:
        db.get(LearnerItem, (1, "cx:lust-haben-auf")).status = "unseen"
        db.commit()
    assert history_api.post(f"{P}/items/cx:lust-haben-auf/practice").status_code == 409
    assert history_api.post(f"{P}/items/nope/practice").status_code == 404
    resp = history_api.post(f"{P}/items/gram:cases/practice")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "item_id": "gram:cases",
        "queued": True,
        "already_queued": False,
        "production_slots": 2,
    }
    again = history_api.post(f"{P}/items/gram:cases/practice").json()
    assert again["already_queued"] is True
    assert get(history_api, "items/gram:cases")["practice_queued"] is True
    with db_of(migrated_settings) as db:
        pending = db.scalars(
            select(RemediationItem).where(
                RemediationItem.item_id == "gram:cases", RemediationItem.consumed_at.is_(None)
            )
        ).all()
        assert len(pending) == 1  # idempotent
        # Memory state only changes through events: nothing was appended or updated.
        assert db.scalar(select(func.count()).select_from(LearningEvent)) == before
        assert {
            (m.item_id, m.facet): (m.mastery, m.last_event_id)
            for m in db.scalars(select(ItemMemory))
        } == memory_before

    session = history_api.post("/api/sessions").json()
    productions = [c for c in session["cards"] if c["type"] == "production"]
    assert productions and productions[0]["item_ids"][0] == "gram:cases"
    assert get(history_api, "items/gram:cases")["practice_queued"] is False  # consumed


# --- history -------------------------------------------------------------------------------------


def test_history_list(history_api: TestClient, seeded: dict) -> None:
    body = get(history_api, "history")
    assert body["total"] == 4 and body["limit"] == 20 and body["offset"] == 0
    kinds = [(r["kind"]) for r in body["items"]]
    assert kinds == ["reading", "session", "session", "session"]  # newest first, no placement
    ids = [r["id"] for r in body["items"]]
    assert seeded["placement_id"] not in ids and seeded["session1"] in ids
    ended = [r["ended_at"] for r in body["items"]]
    assert ended == sorted(ended, reverse=True)

    reading = body["items"][0]
    assert reading["id"] == str(seeded["reading_id"]) and reading["title"] == seeded["text_title"]
    assert reading["words_looked_up"] == 1 and reading["duration_minutes"] == 10.0
    assert reading["cards_answered"] == 1 and reading["correct_rate"] == 1.0

    first = body["items"][-1]
    assert first["id"] == seeded["session1"] and first["title"] is None
    assert first["cards_answered"] == 4 and first["new_items"] == 5
    assert first["correct_rate"] == pytest.approx(2 / 3)  # the intro card is not graded
    assert first["duration_minutes"] == 0.9

    page = get(history_api, "history", limit=2, offset=1)
    assert page["total"] == 4 and [r["id"] for r in page["items"]] == ids[1:3]
    assert get(history_api, "history", limit=2, offset=3)["items"][0]["id"] == ids[3]
    assert get(history_api, "history", offset=10)["items"] == []
    assert history_api.get(f"{P}/history", params={"limit": 0}).status_code == 422


def test_session_detail_lists_the_cards_with_their_answers(
    history_api: TestClient, seeded: dict
) -> None:
    detail = get(history_api, f"history/session/{seeded['session1']}")
    assert detail["cards_answered"] == 4 and detail["new_items"] == 5
    cards = detail["cards"]
    assert len(cards) == 4
    stamps = [c["answered_at"] for c in cards]
    assert stamps == sorted(stamps)
    types = [c["type"] for c in cards]
    assert "grammar_intro" in types and types.count("production") == 2
    intro = next(c for c in cards if c["type"] == "grammar_intro")
    assert intro["outcome"] is None and intro["prompt"] == "Casi" and intro["answer"] is None
    flash = next(c for c in cards if c["type"] == "flashcard_recognition")
    assert (
        flash["options"] and flash["answer"] == flash["expected"] and flash["outcome"] == "correct"
    )
    wrong = next(c for c in cards if c["outcome"] == "error")
    assert wrong["type"] == "production" and wrong["answer"] == "Das Tisch"
    assert wrong["expected"] == "Der Tisch." and wrong["errors"][0]["original"] == "Das Tisch"
    assert {i["item_id"]: i["outcome"] for i in wrong["items"]}["gram:cases"] == "error"
    assert wrong["duration_ms"] == 20000 and wrong["subtype"] == "translation"
    assert wrong["evaluation_id"] == seeded["wrong_evaluation"]

    assert history_api.get(f"{P}/history/session/nope").status_code == 404
    # The placement session is not part of the history.
    assert history_api.get(f"{P}/history/session/{seeded['placement_id']}").status_code == 404
    assert history_api.get(f"{P}/history/session/reading-1").status_code == 404


def test_reading_detail(history_api: TestClient, seeded: dict) -> None:
    detail = get(history_api, f"history/reading/{seeded['reading_id']}")
    assert detail["title"] == seeded["text_title"] and detail["level"] == "A2"
    assert "Tisch" in detail["body"] and detail["duration_minutes"] == 10.0
    (lookup,) = detail["lookups"]
    assert lookup["word"] == "Tisch" and lookup["item_id"] == "lex:tisch"
    assert lookup["label"] == "der Tisch"
    summary = detail["summary"]
    assert summary["subtype"] == "summary" and summary["answer"] == "Der Tisch ist im Haus."
    assert summary["outcome"] == "correct" and summary["feedback"]
    assert summary["items"]
    assert history_api.get(f"{P}/history/reading/99").status_code == 404


# --- contests and voided events ------------------------------------------------------------------


def test_contested_events_are_voided_everywhere(
    history_api: TestClient, migrated_settings: Settings, seeded: dict
) -> None:
    before_items = get(history_api, "items/gram:cases")
    before_week = get(history_api, "activity", days=7)["weeks"][-1]
    before_summary = get(history_api, "summary")
    assert before_items["counts"]["error"] == 2

    resp = history_api.post(
        f"/api/evaluations/{seeded['wrong_evaluation']}/contest", json={"item_ids": []}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["contest"]["verdict"] == "accepted"

    with db_of(migrated_settings) as db:
        voided = {
            e
            for (e,) in db.execute(
                select(LearningEvent.id).where(LearningEvent.voided_by.is_not(None))
            )
        }
        row = db.get(ItemMemory, (1, "gram:cases", "production"))
    assert voided

    after = get(history_api, "items/gram:cases")
    assert after["counts"] == {"correct": 2, "assisted": 0, "error": 1}
    assert after["tag_errors"] == [{"tag": "m", "count": 1}]
    (facet,) = after["facets"]
    trajectory = facet["trajectory"]
    assert not {p["event_id"] for p in trajectory} & voided  # voided events are not replayed
    assert [p["outcome"] for p in trajectory] == [None, "correct", "error", "correct"]
    assert trajectory[-1]["mastery"] == row.mastery and trajectory[-1]["stability"] == row.stability
    assert facet["mastery"] == row.mastery

    # The error rate, the weekly accuracy and the lists drop the voided error.
    listed = get(history_api, "items", kind="grammar", sort="errors")["items"]
    assert listed[0]["errors"] == 1 and listed[0]["top_tags"] == [{"tag": "m", "count": 1}]
    after_week = get(history_api, "activity", days=7)["weeks"][-1]
    assert after_week["production_n"] == before_week["production_n"]
    assert after_week["production_correct_rate"] > before_week["production_correct_rate"]
    # Attempts are still study: streaks and totals do not change.
    after_summary = get(history_api, "summary")
    assert after_summary["streak"] == before_summary["streak"]
    assert after_summary["study_days_total"] == before_summary["study_days_total"]
    assert after_summary["totals"] == before_summary["totals"]

    # History shows the card as it stands now, with the contest.
    card = next(
        c
        for c in get(history_api, f"history/session/{seeded['session1']}")["cards"]
        if c["attempt_id"] == seeded["wrong_attempt"]
    )
    assert card["outcome"] == "correct" and card["evaluation_id"] != seeded["wrong_evaluation"]
    assert card["contest"]["status"] == "resolved" and card["contest"]["verdict"] == "accepted"
    assert card["errors"] == []
    session = get(history_api, "history")["items"][-1]
    assert session["id"] == seeded["session1"] and session["correct_rate"] == 1.0
    recent = after["recent_answers"][-1]
    assert recent["outcome"] == "correct" and recent["contest"]["verdict"] == "accepted"


def test_progress_indexes_exist(migrated_settings: Settings) -> None:
    with db_of(migrated_settings) as db:
        names = {
            r[1] for r in db.connection().exec_driver_sql("SELECT type, name FROM sqlite_master")
        }
    assert {
        "ix_learning_events_learner_ts",
        "ix_attempts_submitted_at",
        "ix_attempts_exercise_id",
        "ix_evaluations_attempt_id",
    } <= names
