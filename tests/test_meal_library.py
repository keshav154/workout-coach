"""Searchable meal library: every past meal reusable, not just the top few."""

import nutrition


def test_lists_all_distinct_meals_including_single_logs(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    # Eight distinct foods, each logged ONCE — meal_quick would show none of
    # these (needs count>=2), but the library must list them all.
    for i in range(8):
        nutrition.log_meal(f"food {i}", calories=100 + i, protein=10)
    r = client.get("/meal_library").get_json()
    names = {m["description"] for m in r["meals"]}
    assert len(names) == 8
    assert r["total"] == 8


def test_dedupes_and_counts(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    nutrition.log_meal("dal chawal", calories=380, protein=15, date_str="2026-09-01")
    nutrition.log_meal("dal chawal", calories=400, protein=16, date_str="2026-09-05")
    r = client.get("/meal_library").get_json()
    assert len(r["meals"]) == 1
    row = r["meals"][0]
    assert row["count"] == 2
    assert row["calories"] == 400        # most-recent version's macros


def test_most_logged_surfaces_first(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    nutrition.log_meal("rare", calories=100, protein=5)
    for _ in range(3):
        nutrition.log_meal("staple", calories=200, protein=12)
    meals = client.get("/meal_library").get_json()["meals"]
    assert meals[0]["description"] == "staple"


def test_search_filter(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    nutrition.log_meal("paneer bhurji", calories=300, protein=18)
    nutrition.log_meal("aloo paratha", calories=280, protein=6)
    r = client.get("/meal_library?q=paneer").get_json()
    assert len(r["meals"]) == 1 and r["meals"][0]["description"] == "paneer bhurji"
