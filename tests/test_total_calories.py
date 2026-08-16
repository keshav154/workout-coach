"""Total-calories handling: parse separately, derive active by subtracting BMR
(never double-count baseline burn against the calorie target)."""

import health


def test_parse_total_calories_into_total_kcal(db):
    health.parse_wearable_payload({
        "total_calories_burned": [
            {"value": 2500, "start_time": "2026-08-10T00:00:00Z"}],
    })
    t = health.health_today("2026-08-10")
    assert t["total_kcal"] == 2500
    assert t.get("active_kcal") is None      # not treated as active


def test_active_derived_from_total_minus_bmr(db, profile_doc):
    p = dict(profile_doc)
    p.update(weight_kg=97, height_cm=178, age=28)
    db["profile"].docs["user"] = p
    # Past day -> full day elapsed -> subtract full BMR.
    health.record_health({"total_kcal": 2500}, "2026-08-01")
    kcal, source = health.active_kcal_today(p, "2026-08-01")
    bmr = 10 * 97 + 6.25 * 178 - 5 * 28 + 5      # 1947.5
    assert source == "total"
    assert kcal == round(2500 - bmr)             # ~553


def test_total_below_bmr_clamps_to_zero(db, profile_doc):
    p = dict(profile_doc)
    db["profile"].docs["user"] = p
    health.record_health({"total_kcal": 500}, "2026-08-01")   # less than a day's BMR
    kcal, source = health.active_kcal_today(p, "2026-08-01")
    assert source == "total" and kcal == 0


def test_watch_active_still_preferred_over_total(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    health.record_health({"active_kcal": 480, "total_kcal": 2500}, "2026-08-01")
    kcal, source = health.active_kcal_today(db["profile"].docs["user"], "2026-08-01")
    assert source == "watch" and kcal == 480


def test_nutrition_data_uses_total_derived_burn(client, db, profile_doc):
    p = dict(profile_doc); p.update(weight_kg=97, height_cm=178, age=28)
    db["profile"].docs["user"] = p
    # Today's total calories (mid-day) -> some active burn should surface.
    health.record_health({"total_kcal": 2200})
    d = client.get("/nutrition_data").get_json()
    assert d["burn_source"] == "total"
    assert d["adjusted_calories"] == d["targets"]["calories"] + d["burned"]
