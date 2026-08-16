"""Full macros: carb/fat targets, storage, totals, and endpoints."""

import agent_core
import nutrition
import write_tools


def test_compute_targets_includes_carbs_and_fat():
    t = agent_core.compute_targets({"weight_kg": 97, "height_cm": 178, "age": 28,
                                    "goal": "recomposition", "activity_level": "sedentary"})
    assert t["fat_target_g"] > 0 and t["carb_target_g"] > 0
    # Macro calories should roughly reconcile with the calorie target
    macro_cals = t["protein_target_g"] * 4 + t["carb_target_g"] * 4 + t["fat_target_g"] * 9
    assert abs(macro_cals - t["calorie_target"]) <= 15


def test_log_meal_stores_and_totals_macros(db):
    nutrition.log_meal("Dal + 2 roti", calories=450, protein=18, carbs=60, fat=10)
    nutrition.log_meal("Paneer", calories=265, protein=18, carbs=6, fat=20)
    t = nutrition.today_totals()
    assert t["calories"] == 715 and t["protein_g"] == 36
    assert t["carbs_g"] == 66 and t["fat_g"] == 30


def test_nutrition_data_returns_macro_targets(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    d = client.get("/nutrition_data").get_json()
    assert d["targets"]["carbs_g"] > 0 and d["targets"]["fat_g"] > 0
    assert "carbs_g" in d["totals"] and "fat_g" in d["totals"]


def test_inline_log_meal_with_macros(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    r = client.post("/log_meal", json={"description": "Rajma chawal", "calories": 450,
                                       "protein_g": 16, "carbs_g": 75, "fat_g": 8})
    assert r.status_code == 200
    t = r.get_json()["totals"]
    assert t["carbs_g"] == 75 and t["fat_g"] == 8


def test_log_meal_tool_with_macros(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    tools = write_tools.make_write_tools({})
    r = tools["log_meal_entry"]("Moong chilla", 250, 14, carbs_g=28, fat_g=8)
    assert "28g carbs" in r and "8g fat" in r
    assert nutrition.today_totals()["carbs_g"] == 28


def test_meal_quick_includes_macros(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    for _ in range(2):
        db["meals"].rows.append({"_id": f"m{_}", "date": "2026-08-15",
                                 "description": "Dal roti", "calories": 400,
                                 "protein_g": 15, "carbs_g": 55, "fat_g": 9})
    q = client.get("/meal_quick").get_json()
    assert q["frequent"][0]["carbs_g"] == 55 and q["frequent"][0]["fat_g"] == 9
