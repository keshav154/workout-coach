"""Calories burned (watch active + cardio) raise the day's calorie budget."""

import agent_core
import cardio
import health


def test_healthsync_active_calories_parsed(db):
    health.parse_wearable_payload({
        "active_calories_burned": [
            {"value": 620, "start_time": "2026-08-16T02:00:00Z"}],
    })
    assert health.health_today("2026-08-16")["active_kcal"] == 620


def test_nutrition_data_adjusts_budget_for_burned(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    health.record_health({"active_kcal": 500})
    cardio.log_cardio("Run", 20, calories=200)
    db["meals"].rows = [{"_id": "m1", "date": agent_core.today_iso(),
                         "description": "lunch", "calories": 800, "protein_g": 40}]
    d = client.get("/nutrition_data").get_json()
    base = d["targets"]["calories"]
    assert d["burned"] == 700                       # 500 watch + 200 cardio
    assert d["adjusted_calories"] == base + 700     # budget raised by burn
    assert d["net_calories"] == 800 - 700           # eaten minus burned


def test_dashboard_adjusted_calorie_target(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    health.record_health({"active_kcal": 400})
    d = client.get("/dashboard").get_json()
    n = d["nutrition"]
    assert n["burned"] == 400 and n["adjusted_target"] == n["cal_target"] + 400


def test_no_burn_no_adjustment(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    d = client.get("/nutrition_data").get_json()
    assert d["burned"] == 0 and d["adjusted_calories"] == d["targets"]["calories"]
