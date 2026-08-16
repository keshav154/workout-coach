"""Active-calorie fallback: estimate from steps when the watch sends no calories
(the real HealthSync payload has steps + heart_rate but no calorie array)."""

import health


REAL_PAYLOAD = {
    "steps": [
        {"count": 2648, "start_time": "2026-08-15T18:30:00Z", "end_time": "2026-08-16T18:29:59.999Z"},
        {"count": 6, "start_time": "2026-08-16T12:37:14Z", "end_time": "2026-08-16T12:37:26Z"},
        {"count": 37, "start_time": "2026-08-16T12:37:26Z", "end_time": "2026-08-16T12:38:31Z"},
    ],
    "heart_rate": [{"bpm": 88, "time": "2026-08-15T13:00:29Z"},
                   {"bpm": 54, "time": "2026-08-16T02:00:00Z"}],
}


def test_real_payload_parses_steps_no_calories(db):
    res = health.parse_wearable_payload(REAL_PAYLOAD)
    assert res["days"] >= 1
    t = health.health_today("2026-08-16")
    assert t["steps"] == 2648            # daily bucket wins over small increments
    assert t.get("active_kcal") is None  # no calorie data in the payload


def test_active_kcal_estimated_from_steps(db, profile_doc):
    p = dict(profile_doc); p["weight_kg"] = 97
    db["profile"].docs["user"] = p
    health.record_health({"steps": 10000})
    kcal, source = health.active_kcal_today(p)
    assert source == "steps"
    # 10000 * 0.04 * (97/70) ≈ 554
    assert 500 <= kcal <= 600


def test_watch_calories_preferred_over_estimate(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    health.record_health({"steps": 10000, "active_kcal": 480})
    kcal, source = health.active_kcal_today()
    assert source == "watch" and kcal == 480


def test_nutrition_data_burn_from_steps(client, db, profile_doc):
    p = dict(profile_doc); p["weight_kg"] = 97
    db["profile"].docs["user"] = p
    health.record_health({"steps": 10000})     # no calorie data, only steps
    d = client.get("/nutrition_data").get_json()
    assert d["burned"] > 0 and d["burn_source"] == "steps"
    assert d["adjusted_calories"] == d["targets"]["calories"] + d["burned"]
