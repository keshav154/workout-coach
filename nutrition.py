"""
Structured meal logging — turns nutrition from chat text into queryable data,
and powers the autonomous daily nutrition nudge.
"""

import logging

from agent_core import _col, load_profile, compute_targets, today_iso

log = logging.getLogger(__name__)


def log_meal(description: str, calories: float = 0, protein: float = 0,
            note: str = "", date_str: str | None = None) -> dict:
    entry = {
        "date":        date_str or today_iso(),
        "description": description,
        "calories":    float(calories or 0),
        "protein_g":   float(protein or 0),
        "note":        note,
    }
    result = _col("meals").insert_one(dict(entry))
    entry["id"] = str(result.inserted_id)
    return entry


def get_meals(date_str: str | None = None) -> list[dict]:
    date_str = date_str or today_iso()
    docs = list(_col("meals").find({"date": date_str}))
    for d in docs:
        d.pop("_id", None)
    return docs


def today_totals(date_str: str | None = None) -> dict:
    meals = get_meals(date_str)
    return {
        "calories": round(sum(m.get("calories", 0) for m in meals)),
        "protein_g": round(sum(m.get("protein_g", 0) for m in meals)),
        "count": len(meals),
    }


def format_nutrition_block(date_str: str | None = None) -> str:
    """Today's logged nutrition vs target, for the coach's system prompt."""
    totals = today_totals(date_str)
    profile = load_profile()
    if not profile:
        return ""
    targets = compute_targets(profile)
    cal_t = targets["calorie_target"]
    prot_t = targets["protein_target_g"]
    if totals["count"] == 0:
        return ("TODAY'S NUTRITION: nothing logged yet today. If the user mentions food, "
                f"log it. Target: {cal_t} kcal, {prot_t}g protein.")
    remaining_cal = cal_t - totals["calories"]
    remaining_p = prot_t - totals["protein_g"]
    return (f"TODAY'S NUTRITION SO FAR: {totals['calories']} kcal, {totals['protein_g']}g protein "
            f"logged across {totals['count']} meal(s). Target: {cal_t} kcal, {prot_t}g protein "
            f"({'over' if remaining_cal < 0 else remaining_cal} kcal remaining, "
            f"{'over' if remaining_p < 0 else remaining_p}g protein remaining).")


def nutrition_summary_text(date_str: str | None = None) -> str:
    """Human-readable !nutrition command output."""
    date_str = date_str or today_iso()
    meals = get_meals(date_str)
    totals = today_totals(date_str)
    profile = load_profile()
    if not meals:
        return f"No meals logged for {date_str} yet. Tell me what you ate, or send a photo."
    lines = [f"Nutrition for {date_str}", ""]
    for m in meals:
        lines.append(f"  {m['description']}: {m['calories']:g} kcal, {m['protein_g']:g}g protein")
    lines.append("")
    lines.append(f"Total: {totals['calories']} kcal | {totals['protein_g']}g protein")
    if profile:
        targets = compute_targets(profile)
        lines.append(f"Target: {targets['calorie_target']} kcal | {targets['protein_target_g']}g protein")
    return "\n".join(lines)
