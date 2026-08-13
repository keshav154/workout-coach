"""Tests for cron dedup, inline logging endpoints, weight parser, undo
coverage, consistency weeks, e1RM, and auth rate limiting."""

from datetime import timedelta

import agent_core
import trust
import write_tools


def test_cron_daily_dedupes_across_retries(client, db, profile_doc, monkeypatch):
    import bot
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    sent = []
    monkeypatch.setattr(bot, "notify", lambda m: sent.append(m) or True)
    r1 = client.get("/cron/daily").get_json()
    r2 = client.get("/cron/daily").get_json()
    assert r1["sent"] is True and len(sent) == 1
    assert r2.get("skipped")


def test_cron_backup_dedupes(client, db, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "send_telegram_document", lambda *a, **k: True)
    r1 = client.get("/cron/backup").get_json()
    r2 = client.get("/cron/backup").get_json()
    assert r1["sent"] is True
    assert r2.get("skipped")


def test_inline_expense_endpoint(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    assert client.post("/log_expense", json={"amount": -5}).status_code == 400
    r = client.post("/log_expense", json={
        "amount": 500, "description": "groceries", "category": "food"})
    assert r.status_code == 200
    row = db["expenses"].rows[0]
    assert row["amount"] == 500 and row["category"] == "Food"
    assert db["audit"].rows[-1]["kind"] == "expense"


def test_inline_meal_and_quick_suggestions(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    today = agent_core.today_iso()
    yday = (agent_core.today() - timedelta(days=1)).isoformat()
    # Two occurrences of the same meal -> becomes a frequent chip; one yesterday
    db["meals"].rows = [
        {"_id": "m1", "date": yday, "description": "Dal + 2 roti", "calories": 350, "protein_g": 15},
        {"_id": "m2", "date": (agent_core.today() - timedelta(days=3)).isoformat(),
         "description": "Dal + 2 roti", "calories": 350, "protein_g": 15},
    ]
    q = client.get("/meal_quick").get_json()
    assert q["frequent"][0]["description"] == "Dal + 2 roti" and q["frequent"][0]["count"] == 2
    assert len(q["yesterday"]) == 1

    assert client.post("/log_meal", json={"description": ""}).status_code == 400
    assert client.post("/log_meal", json={"description": "x", "calories": 99999}).status_code == 400
    r = client.post("/log_meal", json={"description": "Dal + 2 roti",
                                       "calories": 350, "protein_g": 15})
    assert r.status_code == 200
    assert r.get_json()["totals"]["calories"] == 350
    assert any(m.get("date") == today for m in db["meals"].rows)


def test_get_weight_entries_handles_both_formats(db):
    mem = {"weight_log": [
        "2026-08-01: 97.5 kg",
        {"date": "2026-08-05", "kg": 97.0},
        "garbage entry",
        {"date": "bad", "kg": 1},
        "2026-08-03: 97.2 kg feeling good",   # trailing text tolerated
    ]}
    entries = agent_core.get_weight_entries(mem)
    assert entries == [("2026-08-01", 97.5), ("2026-08-03", 97.2), ("2026-08-05", 97.0)]


def test_undo_meal_weight_measurement(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    tools = write_tools.make_write_tools({})

    tools["log_body_weight"](96.8)
    assert db["profile"].docs["user"]["weight_kg"] == 96.8
    out = trust.undo_last()
    assert "Undone" in out
    assert db["profile"].docs["user"]["weight_kg"] == 97       # reverted
    assert agent_core.get_weight_entries(db["memory"].docs.get("mem", {})) == []

    tools["log_meal_entry"]("test meal", 300, 20)
    assert len(db["meals"].rows) == 1
    trust.undo_last()
    assert len(db["meals"].rows) == 0

    tools["log_body_measurement"]("waist", 92)
    assert len(db["measurements"].rows) == 1
    trust.undo_last()
    assert len(db["measurements"].rows) == 0


def test_consistent_weeks():
    today = agent_core.today()
    this_monday = today - timedelta(days=today.weekday())
    sessions = []
    # Two full previous weeks at 3 days/week; current week incomplete
    for wk in (1, 2):
        start = this_monday - timedelta(days=7 * wk)
        for i in range(3):
            sessions.append({"date": (start + timedelta(days=i)).isoformat()})
    log = {"sessions": sessions}
    assert agent_core.get_consistent_weeks(log, 3) == 2
    assert agent_core.get_consistent_weeks(log, 4) == 0
    # Completing the current week extends the streak
    for i in range(3):
        d = this_monday + timedelta(days=i)
        if d <= today:
            sessions.append({"date": d.isoformat()})
    if today.weekday() >= 2:      # current week reachable only from Wednesday on
        assert agent_core.get_consistent_weeks(log, 3) == 3


def test_chart_data_includes_e1rm(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": "2026-08-01", "exercises": [
            {"name": "Bench", "weight": 16, "reps_done": 12}]},
        {"day": "A", "date": "2026-08-07", "exercises": [
            {"name": "Bench", "weight": 18, "reps_done": 10}]},
    ]}
    d = client.get("/chart_data").get_json()
    pts = d["exercises"]["Bench"]
    assert pts[0]["e1rm"] == 22.4      # 16 * (1 + 12/30)
    assert pts[1]["e1rm"] == 24.0      # 18 * (1 + 10/30)


def test_auth_rate_limit(client, db, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "WEB_PASSWORD", "secret")
    bot._AUTH_FAILS.clear()
    for _ in range(10):
        assert client.get("/stats", headers={"X-Password": "wrong"}).status_code == 401
    assert client.get("/stats", headers={"X-Password": "wrong"}).status_code == 429
    # Even the right password is briefly blocked from that IP — by design
    assert client.get("/stats", headers={"X-Password": "secret"}).status_code == 429
    bot._AUTH_FAILS.clear()
    assert client.get("/stats", headers={"X-Password": "secret"}).status_code == 200
