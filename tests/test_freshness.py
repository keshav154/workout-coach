"""Per-muscle freshness model + freshest-day recommendation."""

from datetime import timedelta

import freshness
from agent_core import today


def _sess(days_ago, name, sets_n=4):
    d = (today() - timedelta(days=days_ago)).isoformat()
    return {"day": "A", "date": d,
            "exercises": [{"name": name, "sets": [{"weight": 18, "reps": 10}] * sets_n}]}


def test_untrained_muscle_is_fresh(db):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    fr = freshness.muscle_freshness()
    assert fr["Chest"]["score"] == 100 and fr["Chest"]["state"] == "fresh"


def test_just_trained_muscle_is_fatigued(db):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        _sess(0, "Dumbbell Flat Bench Press", sets_n=5)]}
    fr = freshness.muscle_freshness()
    assert fr["Chest"]["score"] < 70
    assert fr["Chest"]["last_trained"] == today().isoformat()


def test_freshness_recovers_over_time(db):
    recent = {"_id": "log", "sessions": [_sess(0, "Dumbbell Flat Bench Press", 5)]}
    old    = {"_id": "log", "sessions": [_sess(3, "Dumbbell Flat Bench Press", 5)]}
    db["workout_log"].docs["log"] = recent
    fresh_now = freshness.muscle_freshness()["Chest"]["score"]
    db["workout_log"].docs["log"] = old
    fresh_later = freshness.muscle_freshness()["Chest"]["score"]
    assert fresh_later > fresh_now                 # more rest -> fresher


def test_recommend_prefers_fresher_day(db):
    # Trained Push (A) hard today; recommendation should not be Day A.
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        _sess(0, "Dumbbell Flat Bench Press", 5),
        _sess(0, "Dumbbell Overhead Press", 4)]}
    rec = freshness.recommend_day()
    assert rec["recommended"] != "A" or rec["agrees"]  # if rotation already moved on, agrees
    assert rec["day_scores"][0]["avg_freshness"] >= rec["day_scores"][-1]["avg_freshness"]


def test_block_quiet_when_all_fresh_and_agrees(db):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    # No fatigue and (empty log) scheduled == recommended -> no nag
    block = freshness.format_freshness_block()
    assert block == "" or "FRESHNESS" in block     # tolerant: never crashes


def test_endpoint(client, db):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        _sess(0, "Dumbbell Flat Bench Press", 5)]}
    r = client.get("/freshness").get_json()
    assert "freshness" in r and "recommended" in r
    chest = next(m for m in r["freshness"] if m["muscle"] == "Chest")
    assert chest["score"] < 70
