"""Tests for the notification diagnostics: /cron/test, force bypass, and the
clear failure reasons that make a silently-misconfigured channel visible."""

import agent_core


def test_cron_test_reports_delivery_success(client, db, monkeypatch):
    import bot
    sent = []
    monkeypatch.setattr(bot, "notify", lambda m: sent.append(m) or True)
    r = client.get("/cron/test").get_json()
    assert r["sent"] is True and len(sent) == 1
    assert "notify_config" in r


def test_cron_test_reports_delivery_failure(client, db, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "notify", lambda m: False)
    r = client.get("/cron/test").get_json()
    assert r["sent"] is False
    assert "notify_config" in r and "Not delivered" in r["hint"]


def test_notify_config_flags_missing_telegram(monkeypatch):
    import notifier
    monkeypatch.setattr(notifier, "TELEGRAM_BOT_TOKEN", "sometoken")
    monkeypatch.setattr(notifier, "TELEGRAM_CHAT_ID", "")   # the classic mistake
    monkeypatch.setattr(notifier, "TWILIO_ACCOUNT_SID", "")
    cfg = notifier.notify_config()
    assert cfg["channel"] == "none"
    assert cfg["telegram_token_set"] is True
    assert cfg["telegram_chat_id_set"] is False
    assert "TELEGRAM_CHAT_ID" in cfg["hint"]


def test_notify_config_telegram_ready(monkeypatch):
    import notifier
    monkeypatch.setattr(notifier, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(notifier, "TELEGRAM_CHAT_ID", "123")
    cfg = notifier.notify_config()
    assert cfg["channel"] == "telegram" and cfg["hint"] == ""


def test_cron_daily_response_is_small_by_default(client, db, profile_doc, monkeypatch):
    # Cron providers fail a run whose response body is too large, so the default
    # response must NOT echo the full nudge text — only ?debug=1 includes it.
    import bot
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    monkeypatch.setattr(bot, "notify", lambda m: True)
    r = client.get("/cron/daily").get_json()
    assert r["sent"] is True and "message" not in r
    monkeypatch.setattr(bot, "notify", lambda m: True)
    r_dbg = client.get("/cron/daily?force=1&debug=1").get_json()
    assert "message" in r_dbg          # full detail still available on demand


def test_cron_daily_force_bypasses_dedup(client, db, profile_doc, monkeypatch):
    import bot
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    sent = []
    monkeypatch.setattr(bot, "notify", lambda m: sent.append(m) or True)
    client.get("/cron/daily")                      # first send
    r_skip = client.get("/cron/daily").get_json()  # deduped
    assert r_skip.get("skipped")
    r_force = client.get("/cron/daily?force=1").get_json()   # forced re-send
    assert r_force["sent"] is True
    assert len(sent) == 2


def test_cron_daily_reports_notify_failure_reason(client, db, profile_doc, monkeypatch):
    import bot
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    monkeypatch.setattr(bot, "notify", lambda m: False)   # channel misconfigured
    r = client.get("/cron/daily").get_json()
    assert r["sent"] is False and "notify failed" in r["reason"]
    assert "notify_config" in r
    # Not marked done, so a fixed config on a later ping still sends
    assert not client.get("/cron/daily").get_json().get("skipped")


def test_cron_daily_no_nudge_when_trained_today(client, db, profile_doc, monkeypatch):
    import bot
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": agent_core.today_iso(), "exercises": [
            {"name": "Goblet Squat", "weight": 20, "reps_done": 10}]}]}
    calls = []
    monkeypatch.setattr(bot, "notify", lambda m: calls.append(m) or True)
    r = client.get("/cron/daily").get_json()
    assert r["sent"] is False and "no nudge needed" in r["reason"]
    assert calls == []       # nothing sent when already trained


def test_status_shows_notification_channel(client, db, monkeypatch):
    import notifier
    monkeypatch.setattr(notifier, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(notifier, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(notifier, "TWILIO_ACCOUNT_SID", "")
    r = client.get("/status").get_json()
    assert "Notifications: none" in r["status"]
