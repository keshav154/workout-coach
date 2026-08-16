"""Parsing the HealthSync / Samsung Health multi-day array payload."""

import health


# A trimmed version of the real payload the user's sync tool sends.
SAMPLE = {
    "timestamp": "2026-08-16T12:21:33.763147Z",
    "app_version": "1.9.14",
    "steps": [
        {"count": 9652, "start_time": "2026-08-09T18:30:00Z", "end_time": "2026-08-10T18:30:00Z"},
        {"count": 11842, "start_time": "2026-08-11T18:30:00Z", "end_time": "2026-08-12T18:30:00Z"},
        {"count": 1750, "start_time": "2026-08-15T18:30:00Z", "end_time": "2026-08-16T12:21:33Z"},
    ],
    "sleep": [
        {"session_end_time": "2026-08-09T19:24:00Z", "duration_seconds": 8040, "stages": []},
        {"session_end_time": "2026-08-09T22:58:00Z", "duration_seconds": 11100, "stages": []},
    ],
    "heart_rate": [
        {"bpm": 72, "start_time": "2026-08-16T03:00:00Z"},
        {"bpm": 54, "start_time": "2026-08-16T02:00:00Z"},
    ],
}


def test_parse_multiday_steps_by_local_day(db):
    res = health.parse_wearable_payload(SAMPLE)
    assert res["days"] >= 3
    # start_time 2026-08-15T18:30Z == 2026-08-16 00:00 IST -> steps land on Aug 16
    assert health.health_today("2026-08-16")["steps"] == 1750
    # 2026-08-09T18:30Z start == Aug 10 00:00 IST
    assert health.health_today("2026-08-10")["steps"] == 9652
    assert health.health_today("2026-08-12")["steps"] == 11842


def test_parse_sleep_sums_sessions_per_night(db):
    health.parse_wearable_payload(SAMPLE)
    # Both sessions end 2026-08-09 (UTC) -> IST Aug 10; 8040+11100=19140s = 5.3h
    assert health.health_today("2026-08-10")["sleep_hours"] == 5.3


def test_parse_heart_rate_min_is_resting(db):
    health.parse_wearable_payload(SAMPLE)
    # both HR samples are Aug 16 IST; resting ≈ min = 54
    assert health.health_today("2026-08-16")["resting_hr"] == 54


def test_ingest_endpoint_accepts_healthsync(client, db, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "INGEST_TOKEN", "tok")
    r = client.post("/ingest?token=tok", json=SAMPLE).get_json()
    assert r["days_stored"] >= 3 and "warning" not in r
    assert r["stored_today"]  # today's dict present


def test_flat_format_still_works(db):
    res = health.parse_wearable_payload({"steps": 8000, "energy_score": 70})
    assert res["days"] == 1
    assert health.health_today()["steps"] == 8000
