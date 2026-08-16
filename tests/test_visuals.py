"""Backend data for the dashboard activity strip and the Progress heatmap/trends."""

import agent_core


def test_dashboard_week_activity(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    today = agent_core.today_iso()
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": today, "exercises": [
            {"name": "Squat", "weight": 20, "reps_done": 10}]}]}
    d = client.get("/dashboard").get_json()
    wa = d["week_activity"]
    assert len(wa) == 7
    assert wa[-1]["today"] is True and wa[-1]["trained"] is True
    assert all(len(x["dow"]) == 1 for x in wa)


def test_dashboard_activity_marks_rest(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    agent_core.mark_rest_day()
    d = client.get("/dashboard").get_json()
    assert d["week_activity"][-1]["rest"] is True


def test_stats_heatmap_and_deltas(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    today = agent_core.today()
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": today.isoformat(), "exercises": [
            {"name": "Squat", "weight": 20, "reps_done": 10,
             "sets": [{"weight": 20, "reps": 10}]}]}]}
    d = client.get("/stats").get_json()
    assert len(d["heatmap"]) == 35
    last = d["heatmap"][-1]
    assert last["date"] == today.isoformat() and last["volume"] == 200
    assert "dow" in last and 0 <= last["dow"] <= 6


def test_stats_weight_delta_week(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    today = agent_core.today()
    from datetime import timedelta
    mon = today - timedelta(days=today.weekday())
    db["memory"].docs["mem"] = {"_id": "mem", "weight_log": [
        {"date": mon.isoformat(), "kg": 97.0},
        {"date": today.isoformat(), "kg": 96.5}]}
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    d = client.get("/stats").get_json()
    assert d["weight_delta_week"] == -0.5
