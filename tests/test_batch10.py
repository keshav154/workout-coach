"""Cardio logging, watch sleep/energy -> recovery, and strength standards."""

import cardio
import health
import write_tools
from checkin import recovery_score, recovery_summary


def test_cardio_log_and_endpoints(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    r = client.post("/cardio", json={"type": "Treadmill run", "minutes": 30,
                                     "distance_km": 4.2, "calories": 320})
    assert r.status_code == 200
    d = client.get("/cardio").get_json()
    assert d["today"][0]["type"] == "Treadmill run" and d["today"][0]["minutes"] == 30
    assert cardio.cardio_week()["minutes"] == 30
    assert cardio.cardio_today_calories() == 320
    # validation + delete
    assert client.post("/cardio", json={"minutes": 0}).status_code == 400
    cid = d["today"][0]["id"]
    assert client.post("/delete_cardio", json={"id": cid}).status_code == 200
    assert client.get("/cardio").get_json()["today"] == []


def test_cardio_tool(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    tools = write_tools.make_write_tools({})
    r = tools["log_cardio"](minutes=25, type="Walk", distance_km=2.5)
    assert r.startswith("SAVED") and "Walk 25min" in r
    assert cardio.cardio_week()["sessions"] == 1


def test_watch_energy_score_drives_recovery(db):
    health.record_health({"energy_score": 82})
    score, reasons = recovery_score()
    assert score == 8 and any("energy score" in r for r in reasons)
    s = recovery_summary()
    assert s["source"] == "watch" and s["label"] == "push hard"


def test_watch_sleep_used_when_no_energy_score(db):
    health.record_health({"sleep_hours": 5.0})   # poor sleep
    score, _ = recovery_score()
    assert score <= 5                              # baseline 7 minus poor sleep
    assert recovery_summary()["source"] == "watch+checkin"


def test_recovery_falls_back_to_baseline(db):
    s = recovery_summary()
    assert s["source"] == "baseline"


def test_log_health_accepts_sleep_and_energy(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    tools = write_tools.make_write_tools({})
    r = tools["log_health"](sleep_hours=7.5, energy_score=74)
    assert "7.5h sleep" in r and "energy score 74" in r
    assert health.health_today()["energy_score"] == 74


def test_strength_level_on_records(client, db, profile_doc):
    p = dict(profile_doc); p["weight_kg"] = 97
    db["profile"].docs["user"] = p
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": "2026-08-10", "exercises": [
            {"name": "Bench", "weight": 24, "reps_done": 10}]}]}
    d = client.get("/records").get_json()
    rec = d["best"][0]
    assert rec["e1rm"] == 32.0                     # 24 * (1 + 10/30)
    assert rec["level"] in ("Intermediate", "Advanced", "Elite")
    assert rec["ratio"] is not None


def test_dashboard_includes_recovery(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    health.record_health({"energy_score": 60})
    d = client.get("/dashboard").get_json()
    assert d["recovery"]["score"] == 6 and d["recovery"]["source"] == "watch"
