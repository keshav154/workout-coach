"""query_today_workout reports whether today's session is already logged."""

import ask_core
from agent_core import today_iso, get_next_day, load_log


def test_pending_when_nothing_logged_today(db):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": "2026-08-01", "exercises": [
            {"name": "x", "weight": 18, "reps_done": 10}]}]}
    out = ask_core.query_today_workout()
    assert "NOT logged yet" in out
    assert "ALREADY DONE" not in out


def test_reports_done_when_logged_today(db):
    today = today_iso()
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": today, "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 20.5, "reps_done": 10}]}]}
    out = ask_core.query_today_workout()
    assert "ALREADY DONE" in out
    assert "Dumbbell Flat Bench Press" in out
    # the next session shown must be a different (future) day than today's
    nxt = get_next_day(load_log())
    assert f"NEXT session" in out and f"Day {nxt}" in out
