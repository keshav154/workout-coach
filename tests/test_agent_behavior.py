"""
Agent eval harness — regression tests for the coach's BEHAVIOUR, not its
formatting. Each scenario scripts exactly what the LLM 'says' (by stubbing the
tool loop) and asserts the harness does the right thing: logs the right data,
refuses bad data, never leaks reasoning, and never double-acts. This is what
lets prompts change without silently breaking the guardrails.
"""

import pytest

import messaging
from agent_core import load_log, today_iso


@pytest.fixture
def coach(db, profile_doc, monkeypatch):
    """A set-up user, with the LLM tool loop replaced by a scripted reply.
    `coach.say(text)` makes the model 'return' that text next turn."""
    db["profile"].docs["user"] = dict(profile_doc)
    scripted = {"text": ""}
    monkeypatch.setattr(messaging, "reason_loop",
                        lambda *a, **k: scripted["text"])

    class Coach:
        def say(self, text):
            scripted["text"] = text
            return messaging.ask_agent([{"role": "user", "content": "msg"}])
    return Coach()


def test_reasoning_never_leaks_to_user(coach):
    display, *_ = coach.say(
        "Private: user seems tired, keep it short. ===REPLY=== Hey, ready to train?")
    assert display == "Hey, ready to train?"
    assert "Private" not in display


def test_completed_workout_logs_exactly_once(coach, db):
    display, parsed_log, *_ = coach.say(
        '<LOG_SESSION>{"exercises":[{"name":"Dumbbell Flat Bench Press",'
        '"weight":18,"reps_done":10}]}</LOG_SESSION>\n===REPLY=== Logged your bench!')
    sessions = load_log().get("sessions", [])
    assert len(sessions) == 1
    assert sessions[0]["date"] == today_iso()          # code owns the date
    assert parsed_log is not None
    assert "Logged your bench!" in display
    assert "LOG_SESSION" not in display                # raw block stripped


def test_planning_does_not_log(coach, db):
    _, parsed_log, *_ = coach.say(
        "===REPLY=== No problem, let's do legs tomorrow then.")
    assert parsed_log is None
    assert load_log().get("sessions", []) == []


def test_absurd_bodyweight_is_rejected_not_saved(coach, db):
    display, parsed_log, *_ = coach.say(
        '<LOG_SESSION>{"body_weight_kg":999,"exercises":[{"name":"x",'
        '"weight":10,"reps_done":8}]}</LOG_SESSION>\n===REPLY=== done')
    assert parsed_log is None
    assert load_log().get("sessions", []) == []
    assert "⚠️" in display                              # user is told why


def test_relogging_same_day_updates_not_advances(coach, db):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": today_iso(),
         "exercises": [{"name": "Row", "weight": 18, "reps_done": 10}]}]}
    coach.say('<LOG_SESSION>{"exercises":[{"name":"Row","weight":20,'
              '"reps_done":8}]}</LOG_SESSION>\n===REPLY=== updated')
    sessions = load_log().get("sessions", [])
    today_sessions = [s for s in sessions if s["date"] == today_iso()]
    assert len(today_sessions) == 1                     # updated in place
    assert today_sessions[0]["day"] == "A"              # rotation not advanced


def test_valid_expense_is_logged(coach, db):
    display, *_ = coach.say(
        '<LOG_EXPENSE>{"amount":500,"description":"groceries","category":"Food"}'
        '</LOG_EXPENSE>\n===REPLY=== noted')
    assert any(r.get("amount") == 500 for r in db["expenses"].rows)
    assert "500" in display


def test_bad_expense_is_rejected(coach, db):
    # A too-large amount passes the >0 gate but fails validation -> rejected
    # WITH feedback (an amount of 0 is silently ignored, which is also fine).
    display, *_ = coach.say(
        '<LOG_EXPENSE>{"amount":5000000,"description":"x","category":"Food"}'
        '</LOG_EXPENSE>\n===REPLY=== noted')
    assert db["expenses"].rows == []
    assert "⚠️" in display


def test_undo_reverses_the_last_action(coach, db):
    coach.say('<LOG_SESSION>{"exercises":[{"name":"Row","weight":18,'
              '"reps_done":10}]}</LOG_SESSION>\n===REPLY=== logged')
    assert len(load_log().get("sessions", [])) == 1
    display, *_ = coach.say("<UNDO></UNDO>\n===REPLY=== reverting")
    assert load_log().get("sessions", []) == []
    assert "Undone" in display
