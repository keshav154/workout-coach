"""Verified food database: portion parsing, matching, caching, coverage."""

import foods


def test_matches_known_foods_with_quantities(db):
    r = foods.lookup_foods("2 roti and dal")
    items = {m["item"]: m for m in r["matched"]}
    assert items["roti"]["qty"] == 2
    assert items["roti"]["calories"] == 240        # 120 x 2
    assert "dal" in items
    assert r["coverage"] == 1.0
    assert r["totals"]["calories"] == 240 + 180


def test_aliases_and_plural(db):
    assert foods.lookup_foods("2 chapati")["matched"][0]["item"] == "roti"
    assert foods.lookup_foods("3 eggs")["matched"][0]["item"] == "egg"
    assert foods.lookup_foods("chana masala")["matched"][0]["item"] == "chole"


def test_word_quantities_and_half(db):
    r = foods.lookup_foods("half bowl rice")
    assert r["matched"][0]["qty"] == 0.5
    assert r["matched"][0]["calories"] == 100      # 200 x 0.5


def test_partial_coverage_reports_unmatched(db):
    r = foods.lookup_foods("2 roti and dragon fruit salad")
    assert any(m["item"] == "roti" for m in r["matched"])
    assert r["unmatched"]                          # dragon fruit not in DB
    assert 0 < r["coverage"] < 1


def test_longest_substring_match(db):
    # "aloo paratha" should win over "paratha"
    assert foods.lookup_foods("1 aloo paratha")["matched"][0]["item"] == "aloo paratha"


def test_user_cache_is_searched_first(db):
    foods.remember_food("bhel puri", 320, 7, 50, 10, unit="1 plate")
    r = foods.lookup_foods("1 bhel puri")
    assert r["coverage"] == 1.0
    assert r["matched"][0]["calories"] == 320


def test_remember_rejects_insane_values(db):
    foods.remember_food("magic pill", 99999, 0)
    assert foods.lookup_foods("magic pill")["coverage"] == 0.0


def test_read_tool_output(db):
    from ask_core import lookup_food_macros
    out = lookup_food_macros("2 roti and paneer")
    assert "roti" in out and "paneer" in out and "DB subtotal" in out
