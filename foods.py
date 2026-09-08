"""
Verified food database — consistent, repeatable macros for common Indian
vegetarian foods, so logging "2 roti + dal" pulls the SAME numbers every time
instead of a fresh LLM estimate that drifts. Also cuts tokens (no LLM round-trip
for foods we know), which eases rate limits.

Values are per one standard home portion (the UNIT column), from common Indian
nutrition references. The coach calls lookup_foods() as a tool; anything not in
the table it still estimates as before, so coverage grows without blocking.
Learned/custom foods the user logs a lot are cached in the `food_cache`
collection and searched first.
"""

import logging
import re

from agent_core import _col

log = logging.getLogger(__name__)

# name -> (unit, kcal, protein_g, carbs_g, fat_g). Keys are lowercase; aliases
# share a canonical entry via _ALIASES.
FOODS: dict[str, tuple] = {
    # Breads / grains
    "roti":            ("1 medium (chapati)", 120, 3, 18, 3),
    "wheat paratha":   ("1 medium", 210, 5, 26, 9),
    "plain paratha":   ("1 medium", 210, 5, 26, 9),
    "aloo paratha":    ("1 medium", 280, 6, 36, 12),
    "naan":            ("1 piece", 260, 8, 45, 5),
    "rice":            ("1 bowl (150g cooked)", 200, 4, 44, 1),
    "jeera rice":      ("1 bowl", 250, 4, 45, 6),
    "poha":            ("1 bowl", 250, 5, 40, 8),
    "upma":            ("1 bowl", 250, 6, 38, 8),
    "idli":            ("1 piece", 60, 2, 12, 1),
    "dosa":            ("1 plain", 170, 4, 28, 5),
    "bread":           ("1 slice", 70, 3, 13, 1),
    # Dals / legumes
    "dal":             ("1 bowl (150ml)", 180, 9, 24, 5),
    "rajma":           ("1 bowl", 230, 12, 32, 6),
    "chole":           ("1 bowl", 260, 12, 34, 8),
    "chana":           ("1 bowl", 260, 12, 34, 8),
    "sambar":          ("1 bowl", 150, 7, 20, 4),
    # Dairy / protein
    "paneer":          ("100 g", 265, 18, 4, 20),
    "paneer bhurji":   ("1 bowl", 300, 18, 8, 22),
    "curd":            ("1 bowl (150g)", 100, 6, 8, 5),
    "dahi":            ("1 bowl (150g)", 100, 6, 8, 5),
    "milk":            ("1 glass (250ml)", 150, 8, 12, 8),
    "whey":            ("1 scoop", 120, 24, 3, 1),
    "whey protein":    ("1 scoop", 120, 24, 3, 1),
    "egg":             ("1 whole", 78, 6, 1, 5),
    "boiled egg":      ("1 whole", 78, 6, 1, 5),
    "soya chunks":     ("1 bowl cooked", 180, 26, 12, 2),
    "tofu":            ("100 g", 145, 15, 4, 9),
    # Veg / sabzi
    "mixed veg":       ("1 bowl", 150, 4, 18, 7),
    "sabzi":           ("1 bowl", 150, 4, 16, 8),
    "aloo sabzi":      ("1 bowl", 200, 4, 28, 8),
    "bhindi":          ("1 bowl", 160, 3, 14, 10),
    "palak paneer":    ("1 bowl", 280, 14, 12, 20),
    "dal makhani":     ("1 bowl", 320, 12, 30, 16),
    # Snacks / misc
    "banana":          ("1 medium", 105, 1, 27, 0),
    "apple":           ("1 medium", 95, 0, 25, 0),
    "peanuts":         ("handful (30g)", 170, 8, 5, 14),
    "almonds":         ("10 pieces", 70, 3, 2, 6),
    "samosa":          ("1 piece", 260, 5, 30, 14),
    "biscuit":         ("1 piece", 50, 1, 8, 2),
    "tea":             ("1 cup with milk+sugar", 90, 2, 12, 3),
    "coffee":          ("1 cup with milk+sugar", 90, 2, 12, 3),
}

# alias -> canonical key in FOODS
_ALIASES = {
    "chapati": "roti", "chapatti": "roti", "phulka": "roti",
    "paratha": "wheat paratha", "parantha": "wheat paratha",
    "yogurt": "curd", "yoghurt": "curd",
    "protein shake": "whey", "protein scoop": "whey",
    "eggs": "egg", "chickpea": "chole", "chickpeas": "chole",
    "kidney beans": "rajma", "cooked rice": "rice", "steamed rice": "rice",
    "chana masala": "chole",
}

_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
              "five": 5, "half": 0.5}


def _canonical(name: str) -> str | None:
    n = name.strip().lower()
    if n in FOODS:
        return n
    if n in _ALIASES:
        return _ALIASES[n]
    # singular/plural tolerance
    if n.endswith("s") and n[:-1] in FOODS:
        return n[:-1]
    # longest-substring match so "aloo paratha sabzi" hits "aloo paratha"
    best = None
    for key in FOODS:
        if key in n and (best is None or len(key) > len(best)):
            best = key
    return best


def _cached(name: str) -> tuple | None:
    doc = _col("food_cache").find_one({"_id": name.strip().lower()})
    if doc:
        return (doc.get("unit", "1 serving"), doc.get("kcal", 0),
                doc.get("protein_g", 0), doc.get("carbs_g", 0), doc.get("fat_g", 0))
    return None


def remember_food(name: str, kcal: float, protein_g: float,
                  carbs_g: float = 0, fat_g: float = 0, unit: str = "1 serving") -> None:
    """Cache a food the user logs (esp. custom ones) so future logs are
    consistent and token-free. Only stores sane values."""
    name = (name or "").strip().lower()
    if not name or kcal <= 0 or kcal > 3000:
        return
    _col("food_cache").update_one({"_id": name}, {"$set": {
        "unit": unit, "kcal": round(kcal), "protein_g": round(protein_g, 1),
        "carbs_g": round(carbs_g, 1), "fat_g": round(fat_g, 1)}}, upsert=True)


def _parse_items(text: str) -> list[tuple[float, str]]:
    """Split a meal description into (qty, food-phrase) parts."""
    parts = re.split(r",|\band\b|\+|\bwith\b|/|;", (text or "").lower())
    items = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        qty = 1.0
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\s+(.*)$", part)
        if m:
            qty = float(m.group(1)); part = m.group(2).strip()
        else:
            words = part.split()
            if words and words[0] in _NUM_WORDS:
                qty = _NUM_WORDS[words[0]]; part = " ".join(words[1:]).strip()
        # strip filler units the DB portion already implies
        part = re.sub(r"^(bowl|bowls|plate|plates|glass|glasses|piece|pieces|"
                      r"cup|cups|slice|slices|scoop|scoops)\s+of\s+", "", part)
        if part:
            items.append((qty, part))
    return items


def lookup_foods(description: str) -> dict:
    """Match a meal description against the verified DB (+ user cache). Returns
    {matched:[{item, qty, unit, calories, protein_g, carbs_g, fat_g}],
     unmatched:[phrases], totals:{...}, coverage:0..1}. Coverage < 1 means the
     caller should estimate the unmatched parts."""
    matched, unmatched = [], []
    tot = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    for qty, phrase in _parse_items(description):
        entry = _cached(phrase)
        canon = None
        if entry is None:
            canon = _canonical(phrase)
            entry = FOODS.get(canon) if canon else None
        if entry is None:
            unmatched.append(phrase)
            continue
        unit, kcal, p, c, f = entry
        row = {"item": canon or phrase, "qty": qty, "unit": unit,
               "calories": round(kcal * qty), "protein_g": round(p * qty, 1),
               "carbs_g": round(c * qty, 1), "fat_g": round(f * qty, 1)}
        matched.append(row)
        for k, v in (("calories", row["calories"]), ("protein_g", row["protein_g"]),
                     ("carbs_g", row["carbs_g"]), ("fat_g", row["fat_g"])):
            tot[k] += v
    n_parts = len(matched) + len(unmatched)
    coverage = (len(matched) / n_parts) if n_parts else 0.0
    return {"matched": matched, "unmatched": unmatched,
            "totals": {k: round(v, 1) for k, v in tot.items()},
            "coverage": round(coverage, 2)}
