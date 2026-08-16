"""Ingest field aliases + diagnostics, wearable context/tool, and the
under-recovery alert."""

import health


def test_ingest_accepts_aliased_field_names(client, db, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "INGEST_TOKEN", "tok")
    # Common automation key names, none matching the canonical ones exactly
    r = client.post("/ingest?token=tok", json={
        "step_count": 10432, "active_calories": 620, "restingHeartRate": 56,
        "sleep_duration": 7.5, "readiness": 74}).get_json()
    s = r["saved"]
    assert s["steps"] == 10432 and s["active_kcal"] == 620
    assert s["resting_hr"] == 56 and s["sleep_hours"] == 7.5 and s["energy_score"] == 74


def test_ingest_sleep_minutes_to_hours(db):
    saved = health.record_health({"sleep": 450})   # 450 min -> 7.5h
    assert saved["sleep_hours"] == 7.5


def test_ingest_warns_on_unrecognized(client, db, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "INGEST_TOKEN", "tok")
    r = client.post("/ingest?token=tok", json={"weirdField": 5, "another": 9}).get_json()
    assert r["saved"] == {} and "warning" in r
    assert "weirdField" in r["warning"] or "another" in r["warning"]


def test_ingest_get_status(client, db, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "INGEST_TOKEN", "tok")
    health.record_health({"steps": 8000})
    r = client.get("/ingest?token=tok").get_json()
    assert r["today"]["steps"] == 8000


def test_resting_hr_trend_flags_elevated(db):
    # 12 days baseline ~55, last two elevated
    today = health.today()
    from datetime import timedelta
    for i in range(13, 1, -1):
        health.record_health({"resting_hr": 55}, (today - timedelta(days=i)).isoformat())
    health.record_health({"resting_hr": 62}, today.isoformat())
    tr = health.resting_hr_trend()
    assert tr["elevated"] is True and tr["baseline"] == 55


def test_format_wearable_block_net_calories(db):
    health.record_health({"steps": 9000, "active_kcal": 600})
    import cardio
    cardio.log_cardio("Run", 20, calories=200)
    block = health.format_wearable_block()
    assert "9,000 steps" in block and "600 active kcal" in block
    assert "800 extra kcal" in block            # 600 + 200 cardio


def test_query_health_tool(db):
    health.record_health({"steps": 11000, "energy_score": 70, "sleep_hours": 7})
    from ask_core import query_health
    out = query_health()
    assert "steps=11000" in out and "Recovery readiness" in out


def test_recovery_alert_on_low_energy(db, profile_doc):
    import alerts
    db["profile"].docs["user"] = dict(profile_doc)
    today = health.today()
    from datetime import timedelta
    # 3-day training streak
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"date": (today - timedelta(days=i)).isoformat()} for i in range(3)]}
    health.record_health({"energy_score": 35})
    msgs = alerts.run_checks()
    assert any("Recovery flag" in m for m in msgs)
