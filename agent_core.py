"""
All workout logic — program definition, MongoDB storage, system prompt builder.
"""

import os
import re
from datetime import date

from pymongo import MongoClient

# ── MongoDB setup ────────────────────────────────────────────────────────────
_client = None

def _db():
    global _client
    if _client is None:
        uri = os.environ["MONGODB_URI"]
        _client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    return _client["workout_coach"]

def _col(name: str):
    return _db()[name]

# ── User profile ─────────────────────────────────────────────────────────────
USER_PROFILE = {
    "name":             "Keshav",
    "weight_kg":        98,
    "height_cm":        168,
    "diet":             "vegetarian Indian",
    "goal":             "build muscle",
    "level":            "beginner",
    "days_per_week":    4,
    "session_min":      "45-60",
    "tdee_estimate":    2300,
    "calorie_target":   2450,
    "protein_target_g": 160,
}

# ── 4-day body-part split ────────────────────────────────────────────────────
PROGRAM = {
    "A": {
        "name":    "Chest + Triceps",
        "focus":   "chest, triceps",
        "warmup":  "5 min treadmill brisk walk, then 1 light set each exercise",
        "exercises": [
            {"name": "Dumbbell Bench Press",           "sets": 4, "rep_range": "8-12"},
            {"name": "Dumbbell Incline Bench Press",    "sets": 3, "rep_range": "8-12"},
            {"name": "Dumbbell Chest Fly",              "sets": 3, "rep_range": "10-12"},
            {"name": "Tricep Overhead Extension",       "sets": 3, "rep_range": "10-12"},
            {"name": "Resistance Band Tricep Pushdown", "sets": 3, "rep_range": "12-15"},
        ],
    },
    "B": {
        "name":    "Back + Biceps",
        "focus":   "lats, rhomboids, biceps",
        "warmup":  "5 min treadmill brisk walk, then 1 light set each exercise",
        "exercises": [
            {"name": "Dumbbell Bent-Over Row",          "sets": 4, "rep_range": "8-12"},
            {"name": "Dumbbell Single-Arm Row",         "sets": 3, "rep_range": "8-12"},
            {"name": "Resistance Band Lat Pulldown",    "sets": 3, "rep_range": "12-15"},
            {"name": "Resistance Band Face Pull",       "sets": 3, "rep_range": "15"},
            {"name": "Dumbbell Bicep Curl",             "sets": 3, "rep_range": "10-12"},
            {"name": "Hammer Curl",                     "sets": 3, "rep_range": "10-12"},
        ],
    },
    "C": {
        "name":    "Shoulders + Arms",
        "focus":   "deltoids, biceps, triceps",
        "warmup":  "5 min treadmill brisk walk, then band pull-aparts 2x15",
        "exercises": [
            {"name": "Dumbbell Overhead Press",         "sets": 4, "rep_range": "8-12"},
            {"name": "Dumbbell Lateral Raise",          "sets": 3, "rep_range": "12-15"},
            {"name": "Dumbbell Front Raise",            "sets": 3, "rep_range": "12-15"},
            {"name": "Dumbbell Arnold Press",           "sets": 3, "rep_range": "10-12"},
            {"name": "Dumbbell Bicep Curl",             "sets": 3, "rep_range": "10-12"},
            {"name": "Tricep Overhead Extension",       "sets": 3, "rep_range": "10-12"},
        ],
    },
    "D": {
        "name":    "Legs",
        "focus":   "quads, hamstrings, glutes, calves",
        "warmup":  "5 min treadmill incline walk + bodyweight squats 2x15",
        "exercises": [
            {"name": "Goblet Squat",                    "sets": 4, "rep_range": "10-12"},
            {"name": "Romanian Deadlift",               "sets": 3, "rep_range": "10-12"},
            {"name": "Dumbbell Reverse Lunge",          "sets": 3, "rep_range": "10 each"},
            {"name": "Hip Thrust (shoulders on bench)", "sets": 3, "rep_range": "12-15"},
            {"name": "Dumbbell Step-Up (on bench)",     "sets": 3, "rep_range": "10 each"},
            {"name": "Calf Raises",                     "sets": 3, "rep_range": "15-20"},
        ],
    },
}

DAY_ROTATION = ["A", "B", "C", "D"]

DEFAULT_MEMORY = {
    "preferences":        [],
    "injuries_soreness":  [],
    "form_notes":         [],
    "coach_observations": [],
    "personal_records":   [],
    "nutrition_notes":    [],
    "general_notes":      [],
    "weight_log":         [],
}

# ── Workout log (MongoDB) ────────────────────────────────────────────────────
def load_log() -> dict:
    doc = _col("workout_log").find_one({"_id": "log"})
    if doc:
        doc.pop("_id", None)
        return doc
    return {"sessions": []}


def save_log(log: dict) -> None:
    _col("workout_log").update_one(
        {"_id": "log"},
        {"$set": log},
        upsert=True,
    )


def save_session(log: dict, session_data: dict) -> None:
    if not session_data.get("date") or session_data["date"] == "YYYY-MM-DD":
        session_data["date"] = date.today().isoformat()
    # Push new session directly in MongoDB (atomic, no race condition)
    _col("workout_log").update_one(
        {"_id": "log"},
        {"$push": {"sessions": session_data}},
        upsert=True,
    )


def get_next_day(log: dict) -> str:
    sessions = log.get("sessions", [])
    if not sessions:
        return "A"
    last_day = sessions[-1]["day"]
    idx = DAY_ROTATION.index(last_day)
    return DAY_ROTATION[(idx + 1) % len(DAY_ROTATION)]


def get_last_session_for_day(log: dict, day: str) -> dict | None:
    for session in reversed(log.get("sessions", [])):
        if session["day"] == day:
            return session
    return None


# ── Memory (MongoDB) ─────────────────────────────────────────────────────────
def load_memory() -> dict:
    doc = _col("memory").find_one({"_id": "mem"})
    if doc:
        doc.pop("_id", None)
        return doc
    return dict(DEFAULT_MEMORY)


def save_memory(mem: dict) -> None:
    _col("memory").update_one(
        {"_id": "mem"},
        {"$set": mem},
        upsert=True,
    )


def apply_memory_update(mem: dict, update: dict) -> None:
    for key, new_items in update.items():
        if key in mem and isinstance(new_items, list):
            existing = set(mem[key])
            for item in new_items:
                if item not in existing:
                    mem[key].append(item)


def try_parse_memory_update(text: str) -> dict | None:
    match = re.search(r"<UPDATE_MEMORY>\s*(\{.*?\})\s*</UPDATE_MEMORY>", text, re.DOTALL)
    if match:
        try:
            import json
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


# ── Conversation history (MongoDB) ───────────────────────────────────────────
def load_history(source: str = "web") -> list:
    doc = _col("history").find_one({"_id": source})
    return doc.get("messages", []) if doc else []


def save_history(source: str, history: list) -> None:
    _col("history").update_one(
        {"_id": source},
        {"$set": {"messages": history[-20:]}},
        upsert=True,
    )


def reset_history(source: str) -> None:
    _col("history").update_one(
        {"_id": source},
        {"$set": {"messages": []}},
        upsert=True,
    )


# ── Weight trend helpers ─────────────────────────────────────────────────────
def get_weight_trend(mem: dict) -> str:
    entries = mem.get("weight_log", [])
    parsed = []
    for e in entries:
        try:
            d, w = e.split(": ")
            parsed.append((d, float(w.replace(" kg", ""))))
        except (ValueError, AttributeError):
            pass
    if not parsed:
        return "no weight history yet"
    parsed.sort(key=lambda x: x[0])
    latest_date, latest_kg = parsed[-1]
    if len(parsed) == 1:
        return f"last recorded: {latest_kg} kg on {latest_date}"
    first_date, first_kg = parsed[0]
    delta = latest_kg - first_kg
    trend = "gained" if delta > 0 else "lost"
    return (f"last: {latest_kg} kg on {latest_date} | "
            f"{trend} {abs(delta):.1f} kg over {len(parsed)} check-ins "
            f"(from {first_kg} kg on {first_date})")


def get_adjusted_calorie_target(mem: dict, base: int) -> int:
    entries = mem.get("weight_log", [])
    parsed = []
    for e in entries:
        try:
            d, w = e.split(": ")
            parsed.append((d, float(w.replace(" kg", ""))))
        except (ValueError, AttributeError):
            pass
    if len(parsed) < 3:
        return base
    parsed.sort(key=lambda x: x[0])
    delta_per = (parsed[-1][1] - parsed[0][1]) / (len(parsed) - 1)
    if delta_per > 0.7:
        return base - 200
    elif delta_per < 0.1:
        return base + 200
    return base


# ── Prompt builders ──────────────────────────────────────────────────────────
def format_memory_block(mem: dict) -> str:
    labels = {
        "preferences":        "Preferences",
        "injuries_soreness":  "Injuries / soreness",
        "form_notes":         "Form cues",
        "coach_observations": "Coach observations",
        "personal_records":   "Personal records",
        "nutrition_notes":    "Nutrition patterns",
        "general_notes":      "General notes",
    }
    lines = ["--- PERSISTENT MEMORY ---",
             f"Body weight trend: {get_weight_trend(mem)}"]
    for key, label in labels.items():
        items = mem.get(key, [])
        if items:
            lines.append(f"{label}:")
            for item in items[-5:]:
                lines.append(f"  - {item}")
    lines.append("---")
    return "\n".join(lines)


def format_program_block(day: str, last_session: dict | None) -> str:
    p = PROGRAM[day]
    lines = [
        f"TODAY: Day {day} - {p['name']}",
        f"Focus: {p['focus']}",
        f"Warm-up: {p['warmup']}",
        "",
        "Exercises:",
    ]
    for ex in p["exercises"]:
        line = f"  - {ex['name']}  |  {ex['sets']} sets x {ex['rep_range']} reps"
        if last_session:
            prev = next(
                (e for e in last_session.get("exercises", []) if e["name"] == ex["name"]),
                None,
            )
            if prev:
                line += f"  (last: {prev.get('weight','?')}kg x {prev.get('reps_done','?')} reps)"
                try:
                    reps_done = int(str(prev.get("reps_done", "0")).split("-")[0])
                    top_range = int(str(ex["rep_range"]).split("-")[-1].split()[0])
                    if reps_done >= top_range:
                        line += "  -> try +1-2 kg!"
                except (ValueError, AttributeError):
                    pass
        lines.append(line)
    return "\n".join(lines)


def build_system_prompt(day: str, last_session: dict | None, log: dict, mem: dict) -> str:
    p          = USER_PROFILE
    cal_target = get_adjusted_calorie_target(mem, p["calorie_target"])
    sessions   = len(log.get("sessions", []))

    return f"""You are a personal trainer and nutrition coach AI for {p['name']}.
You run as a web chat and Discord bot so keep replies concise and mobile-friendly.
Use plain text only, no markdown symbols.

USER PROFILE:
  Weight: {p['weight_kg']} kg | Height: {p['height_cm']} cm | BMI: {p['weight_kg']/(p['height_cm']/100)**2:.1f}
  Diet: {p['diet']} | Goal: {p['goal']} | Level: {p['level']}
  Equipment: adjustable dumbbells, bench, treadmill, resistance bands
  Calorie target (auto-adjusted): {cal_target} kcal/day
  Protein target: {p['protein_target_g']} g/day
  Sessions logged so far: {sessions}

{format_memory_block(mem)}

{format_program_block(day, last_session)}

YOUR RESPONSIBILITIES:

WEIGHT CHECK-IN (ask at start of every session):
- Ask today's weight casually in one line.
- Compare to last recorded and comment on pace.
- Gaining >0.5 kg/week: "gaining a bit fast, trim 200 kcal"
- No change 2+ sessions: "weight stalled, add 200 kcal"
- 0.1-0.5 kg/week: "perfect pace, keep it up"
- Log in UPDATE_MEMORY weight_log as "YYYY-MM-DD: XX.X kg"

WORKOUT:
- Show today's workout with exercise list, sets, reps.
- Reference past soreness or form cues from memory.
- Suggest warm-up sets before heavy lifts.
- Answer form questions concisely.

NUTRITION (ask after workout):
- Ask what they ate meal by meal.
- Estimate calories + protein per item (Indian portions):
    Dal 1 bowl: 150 kcal, 9g protein
    Roti 1: 100 kcal, 3g protein
    Rice 1 cup cooked: 200 kcal, 4g protein
    Paneer 100g: 265 kcal, 18g protein
    Rajma/Chole 1 bowl: 200 kcal, 12g protein
    Curd 200g: 120 kcal, 7g protein
    Milk 250ml: 150 kcal, 8g protein
    Sabzi 1 serving: 100 kcal, 3g protein
    Tofu 100g: 76 kcal, 8g protein
    Protein powder 1 scoop: 120 kcal, 24g protein
    Paratha 1: 200 kcal, 4g protein
    Sprouts 1 bowl: 80 kcal, 7g protein
    Moong dal chilla 2 pcs: 250 kcal, 14g protein
- Ask smartwatch calories burnt.
- Show summary: eaten vs {cal_target} kcal, protein vs {p['protein_target_g']}g, net calories.
- Suggest 1-2 specific Indian dishes to close protein gap.
  Options: paneer bhurji 150g=30g protein, rajma chawal=16g,
  moong chilla 2pcs=14g, curd+sprouts=14g, milk+peanut butter shake=20g.

LOGGING - output BOTH blocks when session is complete (hidden from user):

<LOG_SESSION>
{{
  "day": "{day}",
  "date": "YYYY-MM-DD",
  "body_weight_kg": 0.0,
  "exercises": [{{"name": "...", "weight": 0, "reps_done": 0}}],
  "nutrition": {{"calories_eaten": 0, "protein_g": 0, "calories_burnt": 0, "net_calories": 0}}
}}
</LOG_SESSION>

<UPDATE_MEMORY>
{{
  "weight_log": ["YYYY-MM-DD: XX.X kg"],
  "personal_records": [],
  "injuries_soreness": [],
  "form_notes": [],
  "nutrition_notes": [],
  "coach_observations": [],
  "preferences": [],
  "general_notes": []
}}
</UPDATE_MEMORY>

Output UPDATE_MEMORY immediately when user mentions weight, injury, PR, or preference.
Only include keys with new items. Omit empty lists.

TONE: encouraging, brief, mobile-friendly. One idea per message.
"""


def try_parse_log(text: str) -> dict | None:
    match = re.search(r"<LOG_SESSION>\s*(\{.*?\})\s*</LOG_SESSION>", text, re.DOTALL)
    if match:
        try:
            import json
            return json.loads(match.group(1))
        except Exception:
            return None
    return None
