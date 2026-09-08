"""Micronutrient tracking derived from logged foods via the verified DB."""

import foods
import nutrition


def test_lookup_returns_micros(db):
    r = foods.lookup_foods("1 paneer and 1 dal")
    assert r["micros"]["calcium_mg"] > 400        # paneer is calcium-rich
    assert r["micros"]["fiber_g"] >= 6            # dal fiber
    assert r["micros"]["b12_ug"] > 0              # paneer b12


def test_micros_scale_with_quantity(db):
    one = foods.lookup_foods("1 roti")["micros"]["fiber_g"]
    three = foods.lookup_foods("3 roti")["micros"]["fiber_g"]
    assert round(three, 1) == round(one * 3, 1)


def test_day_tally_and_flags(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    nutrition.log_meal("2 roti and dal", calories=420, protein=15)
    data = nutrition.micro_totals()
    assert data["micros"]["fiber_g"] > 0
    # B12 near zero from roti+dal -> flagged low
    assert "b12_ug" in data["flags"]


def test_block_only_when_low_and_logged(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    # Nothing logged -> no block
    assert nutrition.format_micro_block() == ""
    nutrition.log_meal("2 roti", calories=240, protein=6)
    block = nutrition.format_micro_block()
    assert "MICRONUTRIENTS RUNNING LOW" in block


def test_endpoint(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    nutrition.log_meal("1 paneer", calories=265, protein=18)
    rows = client.get("/micros").get_json()["micros"]
    assert {r["key"] for r in rows} == {"fiber_g", "iron_mg", "calcium_mg", "b12_ug"}
    cal = next(r for r in rows if r["key"] == "calcium_mg")
    assert cal["value"] > 400
