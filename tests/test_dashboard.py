"""The /dashboard aggregation endpoint."""

import agent_core


def test_dashboard_not_ready_without_profile(client, db):
    assert client.get("/dashboard").get_json() == {"ready": False}


def test_dashboard_aggregates_everything(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    db["memory"].docs["mem"] = {"_id": "mem",
        "weight_log": [{"date": "2026-08-01", "kg": 96.5}]}
    db["meals"].rows = [{"_id": "m1", "date": agent_core.today_iso(),
                         "description": "dal", "calories": 400, "protein_g": 20}]
    import water
    water.add_water(500)
    db["habits"].docs["Log meals"] = {"_id": "Log meals", "created": "2026-08-01"}

    d = client.get("/dashboard").get_json()
    assert d["ready"] is True
    assert d["name"] == "K"
    assert d["workout"]["day"] == "A" and d["workout"]["done_today"] is False
    assert d["water"]["ml"] == 500 and d["water"]["goal"] == 3500
    assert d["nutrition"]["calories"] == 400 and d["nutrition"]["protein_g"] == 20
    assert d["nutrition"]["cal_target"] > 0 and d["nutrition"]["protein_target"] == 194
    assert d["last_weight"] == 96.5
    assert any(h["name"] == "Log meals" for h in d["habits"])


def test_dashboard_marks_done_today(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": agent_core.today_iso(), "exercises": [
            {"name": "Goblet Squat", "weight": 20, "reps_done": 10}]}]}
    d = client.get("/dashboard").get_json()
    assert d["workout"]["done_today"] is True
    assert d["sessions_this_week"] >= 1
