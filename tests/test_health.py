"""Wearable metrics: store, /ingest webhook, /health, and the log_health tool."""

import health
import write_tools


def test_record_health_validates_ranges(db):
    saved = health.record_health({"steps": 10000, "active_kcal": 600, "resting_hr": 58})
    assert saved == {"steps": 10000, "active_kcal": 600, "resting_hr": 58}
    # out-of-range values dropped
    saved = health.record_health({"steps": -5, "resting_hr": 500, "active_kcal": 700})
    assert saved == {"active_kcal": 700}
    assert health.health_today()["active_kcal"] == 700   # last upsert merged


def test_health_week(db):
    health.record_health({"steps": 8000})
    wk = health.health_week()
    assert len(wk) == 7 and wk[-1]["steps"] == 8000 and wk[0]["steps"] is None
    assert health.week_avg_steps() == 8000


def test_ingest_requires_token(client, db, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "INGEST_TOKEN", "sekret")
    assert client.post("/ingest", json={"steps": 5000}).status_code == 401
    assert client.post("/ingest?token=wrong", json={"steps": 5000}).status_code == 401
    r = client.post("/ingest?token=sekret", json={"steps": 5000})
    assert r.status_code == 200 and r.get_json()["saved"]["steps"] == 5000


def test_ingest_not_configured(client, db, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "INGEST_TOKEN", "")
    assert client.post("/ingest", json={"steps": 5000}).status_code == 503


def test_health_endpoints(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    client.post("/health_log", json={"steps": 9000, "active_kcal": 500})
    d = client.get("/health").get_json()
    assert d["today"]["steps"] == 9000 and d["today"]["active_kcal"] == 500
    assert len(d["week"]) == 7


def test_log_health_tool(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    tools = write_tools.make_write_tools({})
    r = tools["log_health"](steps=12000, active_kcal=700, resting_hr=55)
    assert r.startswith("SAVED") and "12,000 steps" in r
    assert health.health_today()["steps"] == 12000
    assert "REJECTED" in tools["log_health"](resting_hr=999)   # nothing valid


def test_dashboard_includes_health(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    health.record_health({"steps": 11000, "active_kcal": 640})
    d = client.get("/dashboard").get_json()
    assert d["health"]["steps"] == 11000 and d["health"]["active_kcal"] == 640
