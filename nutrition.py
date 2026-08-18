"""
Structured meal logging — turns nutrition from chat text into queryable data,
and powers the autonomous daily nutrition nudge.
"""

import logging
from datetime import timedelta

from agent_core import (_col, compute_targets, effective_calorie_target,
                        load_profile, today, today_iso)

log = logging.getLogger(__name__)


def log_meal(description: str, calories: float = 0, protein: float = 0,
            note: str = "", date_str: str | None = None,
            carbs: float = 0, fat: float = 0) -> dict:
    entry = {
        "date":        date_str or today_iso(),
        "description": description,
        "calories":    float(calories or 0),
        "protein_g":   float(protein or 0),
        "carbs_g":     float(carbs or 0),
        "fat_g":       float(fat or 0),
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
        "carbs_g": round(sum(m.get("carbs_g", 0) for m in meals)),
        "fat_g": round(sum(m.get("fat_g", 0) for m in meals)),
        "count": len(meals),
    }


def week_series(days: int = 7) -> list[dict]:
    """Per-day calorie/protein totals for the last `days` days (oldest first)."""
    out = []
    for i in range(days - 1, -1, -1):
        d = (today() - timedelta(days=i)).isoformat()
        t = today_totals(d)
        out.append({"date": d, "calories": t["calories"], "protein_g": t["protein_g"]})
    return out


def plan_remaining_meals(date_str: str | None = None) -> dict:
    """Proactive nutrition: given what's left in today's calorie/macro budget,
    propose concrete meal options that close the gap. Grounded in the user's
    diet + food preferences, and shaped to hit remaining protein first.
    Returns {remaining, options:[{title, items, calories, protein_g, ...}]}
    or {message: ...} when there's nothing meaningful left to plan."""
    import json as _json
    import re as _re

    profile = load_profile()
    if not profile:
        return {"message": "Set up your profile first."}
    targets = compute_targets(profile)
    cal_t = effective_calorie_target(profile, targets)
    totals = today_totals(date_str)

    rem_cal = cal_t - totals["calories"]
    rem_p = targets["protein_target_g"] - totals["protein_g"]
    rem_c = targets.get("carb_target_g", 0) - totals["carbs_g"]
    rem_f = targets.get("fat_target_g", 0) - totals["fat_g"]
    remaining = {"calories": rem_cal, "protein_g": rem_p, "carbs_g": rem_c, "fat_g": rem_f}

    if rem_cal < 150 and rem_p < 15:
        return {"message": "You're essentially at your target for today — no need to plan more.",
                "remaining": remaining}

    from agent_core import load_memory
    mem = load_memory() or {}
    prefs = "; ".join(str(x) for x in (mem.get("preferences") or [])[-6:]) or "none noted"
    diet = profile.get("diet", "vegetarian Indian")
    eaten = ", ".join(m.get("description", "") for m in get_meals(date_str)) or "nothing yet"

    prompt = (
        "You are a nutrition coach. Propose 2 meal options that fit the user's "
        "REMAINING budget for the rest of today. Prioritise hitting the remaining "
        "protein without going over the remaining calories.\n"
        f"Diet: {diet} (respect it strictly).\n"
        f"Food preferences/likes: {prefs}.\n"
        f"Already eaten today: {eaten}.\n"
        f"REMAINING today — calories: {rem_cal}, protein: {rem_p}g, carbs: {rem_c}g, fat: {rem_f}g.\n"
        "Each option should be a realistic single meal (or meal + snack) with common "
        "home portions. Return ONLY a JSON array of exactly 2 objects:\n"
        '[{"title":"<short name>","items":"<what to eat, plain>","calories":<int>,'
        '"protein_g":<int>,"carbs_g":<int>,"fat_g":<int>}]'
    )
    try:
        from llm import chat
        raw = chat([{"role": "system", "content": "You output only a strict JSON array."},
                    {"role": "user", "content": prompt}], temperature=0.5)
        m = _re.search(r"\[.*\]", raw or "", _re.DOTALL)
        items = _json.loads(m.group(0)) if m else []
    except Exception as e:
        log.warning(f"plan_remaining_meals error: {e}")
        return {"message": "Couldn't plan meals right now — try again in a moment.",
                "remaining": remaining}

    options = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            options.append({
                "title": str(it.get("title") or "Option")[:60],
                "items": str(it.get("items") or "")[:200],
                "calories": int(float(it.get("calories") or 0)),
                "protein_g": int(float(it.get("protein_g") or 0)),
                "carbs_g": int(float(it.get("carbs_g") or 0)),
                "fat_g": int(float(it.get("fat_g") or 0)),
            })
        except (TypeError, ValueError):
            continue
        if len(options) >= 2:
            break
    if not options:
        return {"message": "Couldn't plan meals right now — try again in a moment.",
                "remaining": remaining}
    return {"remaining": remaining, "options": options}


def format_nutrition_block(date_str: str | None = None) -> str:
    """Today's logged nutrition vs target, for the coach's system prompt."""
    totals = today_totals(date_str)
    profile = load_profile()
    if not profile:
        return ""
    targets = compute_targets(profile)
    cal_t = effective_calorie_target(profile, targets)
    tdee_note = ("" if targets.get("tdee_source") != "measured" else
                 f" (target is built on your MEASURED maintenance ~{targets['tdee']} kcal, "
                 "learned from your logged food and weight trend — not a generic formula)")
    prot_t = targets["protein_target_g"]
    carb_t = targets.get("carb_target_g", 0)
    fat_t = targets.get("fat_target_g", 0)
    if totals["count"] == 0:
        return ("TODAY'S NUTRITION: nothing logged yet today. If the user mentions food, "
                f"log it (estimate calories, protein, carbs and fat). "
                f"Target: {cal_t} kcal, {prot_t}g protein, {carb_t}g carbs, {fat_t}g fat{tdee_note}.")
    remaining_cal = cal_t - totals["calories"]
    remaining_p = prot_t - totals["protein_g"]
    return (f"TODAY'S NUTRITION SO FAR: {totals['calories']} kcal, {totals['protein_g']}g protein, "
            f"{totals['carbs_g']}g carbs, {totals['fat_g']}g fat "
            f"logged across {totals['count']} meal(s). Target: {cal_t} kcal, {prot_t}g protein, "
            f"{carb_t}g carbs, {fat_t}g fat{tdee_note} "
            f"({'over' if remaining_cal < 0 else remaining_cal} kcal remaining, "
            f"{'over' if remaining_p < 0 else remaining_p}g protein remaining).")
