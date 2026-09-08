"""Cron-free weekly learning: shared job runner + on-app-open trigger."""

import bot
from agent_core import today as _today


def _week_key():
    return "{}-W{:02d}".format(*_today().isocalendar()[:2])


def test_run_weekly_jobs_marks_done_and_returns_summary(db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    monkeypatch.setattr(bot, "notify", lambda m: True)
    from monitor import job_done
    assert job_done("cron_weekly", _week_key()) is False
    result = bot._run_weekly_jobs(send_report=True)
    assert "sent" in result and "tuned" in result
    assert job_done("cron_weekly", _week_key()) is True   # slot claimed


def test_run_weekly_jobs_can_skip_report(db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    sent = []
    monkeypatch.setattr(bot, "notify", lambda m: sent.append(m) or True)
    bot._run_weekly_jobs(send_report=False)
    assert sent == []                                     # no Telegram push


def test_lazy_trigger_noop_without_real_db(db, profile_doc, monkeypatch):
    # Tests have no MONGODB_URI, so the lazy trigger must not spawn a thread.
    monkeypatch.delenv("MONGODB_URI", raising=False)
    started = []
    import threading
    real = threading.Thread
    monkeypatch.setattr(threading, "Thread",
                        lambda *a, **k: started.append(1) or real(target=lambda: None))
    bot.maybe_run_weekly_lazily()
    assert started == []


def test_cron_weekly_still_dedupes(client, db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    monkeypatch.setattr(bot, "notify", lambda m: True)
    r1 = client.get("/cron/weekly").get_json()
    assert "sent" in r1
    r2 = client.get("/cron/weekly").get_json()
    assert r2.get("skipped")                              # once per week
