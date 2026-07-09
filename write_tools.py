"""
Write tools for the agentic loop. Unlike the legacy hidden-block approach
(model emits <LOG_SESSION> text that gets regex-parsed AFTER the reply), these
are real function tools: the model calls them mid-turn, the write is validated
and executed immediately, and the RESULT (success, PRs, or a validation error)
is returned to the model so it can react honestly in the same reply.

make_write_tools(ctx) builds per-call implementations bound to a context dict
so messaging.ask_agent can see what actions actually happened.
"""

import logging

from agent_core import (
    apply_memory_update,
    detect_prs,
    get_next_day,
    load_log,
    load_memory,
    load_profile,
    save_memory,
    save_profile,
    save_session,
    today_iso,
)
from trust import record_audit, undo_last, validate_expense, validate_session
from expense_core import log_expense, save_budget
from nutrition import log_meal
from checkin import save_checkin, format_checkin_block
from goals import clear_goals, goals_status, set_goal
from progression import clear_autodeload_flag, get_autodeload_flags

log = logging.getLogger(__name__)


WRITE_TOOLS = [
    {"type": "function", "function": {
        "name": "log_workout_session",
        "description": "Save today's completed workout. Call ONLY when the user clearly finished training (never for plans or skips). Pass every exercise they reported with weight (kg) and reps of their top set.",
        "parameters": {"type": "object", "properties": {
            "exercises": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "weight": {"type": "number", "description": "kg used (0 for bodyweight moves)"},
                "reps_done": {"type": "integer"}},
                "required": ["name"]}},
            "body_weight_kg": {"type": "number", "description": "today's body weight if mentioned"},
            "calories_eaten": {"type": "number"}, "protein_g": {"type": "number"},
            "calories_burnt": {"type": "number"}},
            "required": ["exercises"]},
    }},
    {"type": "function", "function": {
        "name": "log_body_weight",
        "description": "Record the user's body weight check-in (kg). Call whenever they state their current weight.",
        "parameters": {"type": "object", "properties": {
            "kg": {"type": "number"}}, "required": ["kg"]},
    }},
    {"type": "function", "function": {
        "name": "log_meal_entry",
        "description": "Log something the user ate, any time of day. Estimate calories and protein from Indian portions if they didn't give numbers.",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string"},
            "calories": {"type": "number"},
            "protein_g": {"type": "number"}},
            "required": ["description", "calories", "protein_g"]},
    }},
    {"type": "function", "function": {
        "name": "log_expense_entry",
        "description": "Log money the user spent. Categories: Food, Transport, Bills, Shopping, Health, Entertainment, Other. Never use for body weight, reps, or other non-money numbers.",
        "parameters": {"type": "object", "properties": {
            "amount": {"type": "number", "description": "rupees"},
            "description": {"type": "string"},
            "category": {"type": "string"}},
            "required": ["amount", "description", "category"]},
    }},
    {"type": "function", "function": {
        "name": "save_daily_checkin",
        "description": "Record wellness check-in when the user mentions sleep, energy or soreness. Omit fields they didn't mention.",
        "parameters": {"type": "object", "properties": {
            "sleep_hours": {"type": "number"},
            "energy": {"type": "integer", "description": "1-10"},
            "soreness": {"type": "integer", "description": "1-10"}}},
    }},
    {"type": "function", "function": {
        "name": "set_user_goal",
        "description": "Set a goal when the user states one: body-weight target (kind='weight') or a lift target (kind='lift').",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["weight", "lift"]},
            "target": {"type": "number"},
            "by_date": {"type": "string", "description": "YYYY-MM-DD, optional"},
            "exercise": {"type": "string", "description": "for lift goals"}},
            "required": ["kind", "target"]},
    }},
    {"type": "function", "function": {
        "name": "clear_user_goals",
        "description": "Delete all goals. Only when the user explicitly asks to clear/reset their goals.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "set_category_budget",
        "description": "Set a monthly spending budget for a category.",
        "parameters": {"type": "object", "properties": {
            "category": {"type": "string"},
            "amount": {"type": "number"}},
            "required": ["category", "amount"]},
    }},
    {"type": "function", "function": {
        "name": "update_training_days",
        "description": "Change how many days per week the user trains (1-7).",
        "parameters": {"type": "object", "properties": {
            "days_per_week": {"type": "integer"}}, "required": ["days_per_week"]},
    }},
    {"type": "function", "function": {
        "name": "undo_last_action",
        "description": "Reverse the most recently logged item (session or expense). Only when the user asks to undo/remove/delete the last thing.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "record_memory_note",
        "description": "Save a durable observation about the user for future sessions: injuries/soreness, preferences, form cues, nutrition patterns, or coach observations.",
        "parameters": {"type": "object", "properties": {
            "category": {"type": "string", "enum": [
                "injuries_soreness", "preferences", "form_notes",
                "nutrition_notes", "coach_observations", "general_notes"]},
            "note": {"type": "string"}},
            "required": ["category", "note"]},
    }},
    {"type": "function", "function": {
        "name": "record_lesson",
        "description": "Call whenever the user CORRECTS you — wrong assumption, misread intent, wrong number, anything. Store what you got wrong and the rule to apply next time. This is how you improve.",
        "parameters": {"type": "object", "properties": {
            "mistake": {"type": "string", "description": "what you got wrong, briefly"},
            "rule": {"type": "string", "description": "the generalized rule to follow in future"}},
            "required": ["mistake", "rule"]},
    }},
]


def make_write_tools(ctx: dict) -> dict:
    """Build tool implementations bound to a per-call context dict.
    ctx collects what actually happened so ask_agent can add UI suffixes."""

    def log_workout_session(exercises: list, body_weight_kg: float | None = None,
                            calories_eaten: float | None = None, protein_g: float | None = None,
                            calories_burnt: float | None = None) -> str:
        workout_log = load_log()
        day = get_next_day(workout_log)
        session = {"day": day, "date": today_iso(), "exercises": exercises or []}
        if body_weight_kg:
            session["body_weight_kg"] = body_weight_kg
        nutrition = {}
        if calories_eaten: nutrition["calories_eaten"] = calories_eaten
        if protein_g:      nutrition["protein_g"] = protein_g
        if calories_burnt: nutrition["calories_burnt"] = calories_burnt
        if nutrition:
            session["nutrition"] = nutrition

        ok, reason, cleaned = validate_session(session)
        if not ok:
            return f"REJECTED — not saved: {reason} Ask the user to confirm the numbers."
        cleaned["date"] = today_iso()
        cleaned["day"]  = day
        prs = detect_prs(workout_log, cleaned)
        save_session(workout_log, cleaned)
        record_audit("session", f"Day {day} on {cleaned['date']}",
                     ref={"date": cleaned["date"], "day": day})
        if get_autodeload_flags():
            for e in cleaned.get("exercises", []):
                if e.get("name"):
                    clear_autodeload_flag(e["name"])
        if body_weight_kg:
            mem = load_memory()
            apply_memory_update(mem, {"weight_log": [f"{today_iso()}: {float(body_weight_kg):.1f} kg"]})
            save_memory(mem)
        ctx["session"] = cleaned
        ctx["prs"] = prs
        result = f"SAVED: Day {day} session on {cleaned['date']} with {len(cleaned.get('exercises', []))} exercise(s)."
        if prs:
            result += " NEW PRs: " + "; ".join(prs)
        return result

    def log_body_weight(kg: float) -> str:
        if not (30 <= float(kg) <= 300):
            return f"REJECTED: {kg} kg is outside the sane range (30-300). Confirm with the user."
        mem = load_memory()
        apply_memory_update(mem, {"weight_log": [f"{today_iso()}: {float(kg):.1f} kg"]})
        save_memory(mem)
        profile = load_profile() or {}
        profile["weight_kg"] = float(kg)
        save_profile(profile)
        ctx.setdefault("notes", []).append(f"⚖️ Weight logged: {float(kg):.1f} kg")
        return f"SAVED: body weight {float(kg):.1f} kg on {today_iso()}."

    def log_meal_entry(description: str, calories: float, protein_g: float) -> str:
        if calories and calories > 12000:
            return "REJECTED: calories over 12000 look wrong — confirm with the user."
        log_meal(description, calories or 0, protein_g or 0)
        ctx.setdefault("notes", []).append("🍽️ Meal logged.")
        from nutrition import today_totals
        t = today_totals()
        return (f"SAVED meal '{description}'. Today so far: {t['calories']} kcal, "
                f"{t['protein_g']}g protein across {t['count']} meal(s).")

    def log_expense_entry(amount: float, description: str, category: str) -> str:
        ok, reason = validate_expense(amount)
        if not ok:
            return f"REJECTED — not saved: {reason}"
        entry = log_expense(amount=float(amount), description=description,
                            category=(category or "Other").capitalize())
        record_audit("expense", f"Rs {entry['amount']:,.0f} {entry['category']} — {description}",
                     ref=entry.get("id"))
        ctx.setdefault("notes", []).append(
            f"💸 Logged Rs {entry['amount']:,.0f} under {entry['category']}.")
        return f"SAVED: Rs {entry['amount']:,.0f} under {entry['category']}."

    def save_daily_checkin(sleep_hours: float | None = None, energy: int | None = None,
                           soreness: int | None = None) -> str:
        if sleep_hours is None and energy is None and soreness is None:
            return "Nothing to save — no fields given."
        save_checkin(sleep_hours=sleep_hours, energy=energy, soreness=soreness)
        ctx.setdefault("notes", []).append("✅ Check-in saved.")
        return "SAVED. " + format_checkin_block()

    def set_user_goal(kind: str, target: float, by_date: str | None = None,
                      exercise: str | None = None) -> str:
        set_goal(kind=kind, target=target, by_date=by_date, exercise=exercise)
        ctx.setdefault("notes", []).append("🎯 Goal set.")
        return "SAVED. Current goals:\n" + goals_status()

    def clear_user_goals() -> str:
        n = clear_goals()
        ctx.setdefault("notes", []).append(f"🗑️ Cleared {n} goal(s).")
        return f"Cleared {n} goal(s)."

    def set_category_budget(category: str, amount: float) -> str:
        save_budget(str(category).capitalize(), float(amount))
        ctx.setdefault("notes", []).append(
            f"💰 Budget set: {category} = Rs {float(amount):,.0f}/month.")
        return f"SAVED: {category} budget Rs {float(amount):,.0f}/month."

    def update_training_days(days_per_week: int) -> str:
        if not (1 <= int(days_per_week) <= 7):
            return "REJECTED: days per week must be 1-7."
        profile = load_profile() or {}
        profile["days_per_week"] = int(days_per_week)
        save_profile(profile)
        ctx.setdefault("notes", []).append(f"📅 Now training {days_per_week} days/week.")
        return f"SAVED: training {days_per_week} days/week."

    def undo_last_action() -> str:
        result = undo_last()
        ctx.setdefault("notes", []).append("↩️ " + result)
        return result

    def record_memory_note(category: str, note: str) -> str:
        mem = load_memory()
        apply_memory_update(mem, {category: [note]})
        save_memory(mem)
        return f"Remembered under {category}: {note}"

    def record_lesson(mistake: str, rule: str) -> str:
        from memory_core import save_lesson
        save_lesson(mistake, rule)
        ctx.setdefault("notes", []).append("📚 Lesson learned.")
        return f"Lesson recorded — will apply in future: {rule}"

    return {
        "log_workout_session":  log_workout_session,
        "log_body_weight":      log_body_weight,
        "log_meal_entry":       log_meal_entry,
        "log_expense_entry":    log_expense_entry,
        "save_daily_checkin":   save_daily_checkin,
        "set_user_goal":        set_user_goal,
        "clear_user_goals":     clear_user_goals,
        "set_category_budget":  set_category_budget,
        "update_training_days": update_training_days,
        "undo_last_action":     undo_last_action,
        "record_memory_note":   record_memory_note,
        "record_lesson":        record_lesson,
    }
