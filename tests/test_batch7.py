"""Tests for rest days, deload surfacing, per-exercise notes, weekly summary,
the 20.5 kg dumbbell, and Telegram send retry."""

import agent_core
import write_tools


def test_dumbbell_list_uses_20_5():
    assert 20.5 in agent_core.AVAILABLE_DUMBBELLS
    assert 20 not in agent_core.AVAILABLE_DUMBBELLS


def test_mark_rest_day_and_nudge_suppressed(db, profile_doc):
    import reports
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    assert reports.build_daily_nudge() is not None      # would nudge normally
    agent_core.mark_rest_day()
    assert agent_core.is_rest_day() is True
    assert reports.build_daily_nudge() is None           # rest day -> no nudge
    agent_core.unmark_rest_day()
    assert agent_core.is_rest_day() is False


def test_rest_day_tool(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    r = write_tools.make_write_tools({})["mark_rest_day"]()
    assert r.startswith("SAVED") and agent_core.is_rest_day()


def test_rest_day_bridges_streak(db):
    from datetime import timedelta
    today = agent_core.today()
    # Trained 3 days, then a rest day yesterday, then trained today's-2..
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"date": (today - timedelta(days=3)).isoformat()},
        {"date": (today - timedelta(days=2)).isoformat()},
        {"date": today.isoformat()},
    ]}
    # Mark yesterday a rest day so the streak bridges across it
    agent_core.mark_rest_day((today - timedelta(days=1)).isoformat())
    streak = agent_core.get_consecutive_workout_days(db["workout_log"].docs["log"])
    assert streak == 3        # 3 training days, rest day bridges but doesn't add


def test_rest_day_endpoint_toggles(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    r1 = client.post("/rest_day").get_json()
    assert r1["rest_day"] is True and agent_core.is_rest_day()
    r2 = client.post("/rest_day").get_json()
    assert r2["rest_day"] is False and not agent_core.is_rest_day()


def test_today_program_exposes_deload_and_rest(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    # 24 sessions triggers should_suggest_deload
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": f"2026-06-{(i % 28) + 1:02d}", "exercises": []}
        for i in range(24)]}
    d = client.get("/today_program").get_json()
    assert d["deload"] is True
    assert "rest_day" in d and "autodeload" in d and "rest_suggested" in d


def test_log_workout_stores_per_exercise_note(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    r = client.post("/log_workout", json={"day": "A", "exercises": [
        {"name": "Dumbbell Flat Bench Press", "sets": [{"weight": 18, "reps": 10}],
         "note": "left shoulder felt tight"}]})
    assert r.status_code == 200
    ex = db["workout_log"].docs["log"]["sessions"][-1]["exercises"][0]
    assert ex["note"] == "left shoulder felt tight"
    # Surfaced in stats detail
    detail = client.get("/stats").get_json()["recent_sessions"][0]["detail"]
    assert detail[0]["note"] == "left shoulder felt tight"


def test_weekly_summary_endpoint(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    today = agent_core.today()
    from datetime import timedelta
    wk_start = today - timedelta(days=today.weekday())
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": wk_start.isoformat(), "duration_min": 50, "exercises": [
            {"name": "Bench", "weight": 18, "reps_done": 10,
             "sets": [{"weight": 18, "reps": 10}]}]}]}
    d = client.get("/weekly_summary").get_json()
    assert d["sessions_done"] == 1
    assert d["total_minutes"] == 50
    assert d["total_volume"] == 180
    assert d["week_start"] == wk_start.isoformat()


def test_telegram_send_retries_then_succeeds(monkeypatch):
    import notifier
    monkeypatch.setattr(notifier, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(notifier, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)  # no real backoff
    calls = {"n": 0}

    class Resp:
        def __init__(self, code): self.status_code = code; self.text = ""

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        return Resp(500 if calls["n"] == 1 else 200)   # fail once, then succeed

    monkeypatch.setattr(notifier.requests, "post", fake_post)
    assert notifier.send_telegram("hi") is True
    assert calls["n"] == 2


def test_telegram_send_no_retry_on_4xx(monkeypatch):
    import notifier
    monkeypatch.setattr(notifier, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(notifier, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class Resp:
        def __init__(self, code): self.status_code = code; self.text = "bad request"

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        return Resp(400)      # a bad token/chat — retrying is pointless

    monkeypatch.setattr(notifier.requests, "post", fake_post)
    assert notifier.send_telegram("hi") is False
    assert calls["n"] == 1
