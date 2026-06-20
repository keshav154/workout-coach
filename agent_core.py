"""
All workout logic — program definition, MongoDB storage, system prompt builder.
Profile is stored in MongoDB; onboarding collects it on first run via chat.
"""

import json
import os
import re
from datetime import date, datetime, timedelta

import certifi
from pymongo import MongoClient

# ── MongoDB setup ────────────────────────────────────────────────────────────
_client = None

def _db():
    global _client
    if _client is None:
        uri = os.environ["MONGODB_URI"]
        _client = MongoClient(
            uri,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=10000,
        )
    return _client["workout_coach"]

def _col(name: str):
    return _db()[name]

# ── User profile (MongoDB) ────────────────────────────────────────────────────
def load_profile() -> dict | None:
    doc = _col("profile").find_one({"_id": "user"})
    if doc:
        doc.pop("_id", None)
        return doc
    return None

def save_profile(profile: dict) -> None:
    _col("profile").update_one(
        {"_id": "user"},
        {"$set": profile},
        upsert=True,
    )

def profile_complete(profile: dict | None) -> bool:
    if not profile:
        return False
    required = ["name", "age", "weight_kg", "height_cm", "goal", "level",
                "days_per_week", "diet", "session_min", "activity_level"]
    return all(profile.get(k) for k in required)

def compute_targets(profile: dict) -> dict:
    """Compute TDEE, calorie target, and protein target from profile."""
    w = float(profile.get("weight_kg", 80))
    h = float(profile.get("height_cm", 170))
    a = int(profile.get("age", 25))
    # Mifflin-St Jeor for males (default to male; can be extended)
    bmr = 10 * w + 6.25 * h - 5 * a + 5
    # Activity multiplier: sedentary=1.2, lightly active=1.375, moderately active=1.55
    activity = profile.get("activity_level", "sedentary").lower()
    if "moderate" in activity:
        multiplier = 1.55
    elif "light" in activity:
        multiplier = 1.375
    else:
        multiplier = 1.2
    tdee = int(bmr * multiplier)
    goal = profile.get("goal", "recomposition").lower()
    if "lose" in goal or "fat" in goal or "cut" in goal:
        cal_target = tdee - 300
    elif "gain" in goal or "muscle" in goal or "bulk" in goal:
        cal_target = tdee + 200
    else:  # recomposition
        cal_target = tdee
    protein_g = int(w * 2.0)  # 2g per kg bodyweight
    return {
        "tdee": tdee,
        "calorie_target": cal_target,
        "protein_target_g": protein_g,
    }

# ── 4-day body-part split (no treadmill; incline-decline bench) ──────────────
PROGRAM = {
    "A": {
        "name":    "Chest + Triceps",
        "focus":   "chest, triceps",
        "warmup":  "5 min treadmill brisk walk, then 1 light set each exercise",
        "exercises": [
            {"name": "Dumbbell Flat Bench Press",          "sets": 4, "rep_range": "8-12"},
            {"name": "Dumbbell Incline Bench Press",        "sets": 3, "rep_range": "8-12"},
            {"name": "Dumbbell Decline Bench Press",        "sets": 3, "rep_range": "8-12"},
            {"name": "Dumbbell Chest Fly (flat)",           "sets": 3, "rep_range": "10-12"},
            {"name": "Tricep Overhead Extension",           "sets": 3, "rep_range": "10-12"},
            {"name": "Resistance Band Tricep Pushdown",     "sets": 3, "rep_range": "12-15"},
        ],
    },
    "B": {
        "name":    "Back + Biceps",
        "focus":   "lats, rhomboids, rear delts, biceps",
        "warmup":  "5 min treadmill brisk walk, then band pull-aparts 2x15",
        "exercises": [
            {"name": "Dumbbell Bent-Over Row",              "sets": 4, "rep_range": "8-12"},
            {"name": "Dumbbell Single-Arm Row",             "sets": 3, "rep_range": "8-12"},
            {"name": "Resistance Band Lat Pulldown",        "sets": 3, "rep_range": "12-15"},
            {"name": "Resistance Band Face Pull",           "sets": 3, "rep_range": "15"},
            {"name": "Dumbbell Bicep Curl",                 "sets": 3, "rep_range": "10-12"},
            {"name": "Hammer Curl",                         "sets": 3, "rep_range": "10-12"},
        ],
    },
    "C": {
        "name":    "Shoulders + Arms",
        "focus":   "deltoids, biceps, triceps",
        "warmup":  "5 min treadmill brisk walk, then band pull-aparts 2x15",
        "exercises": [
            {"name": "Dumbbell Overhead Press",             "sets": 4, "rep_range": "8-12"},
            {"name": "Dumbbell Lateral Raise",              "sets": 3, "rep_range": "12-15"},
            {"name": "Dumbbell Front Raise",                "sets": 3, "rep_range": "12-15"},
            {"name": "Resistance Band Rear Delt Fly",       "sets": 3, "rep_range": "15"},
            {"name": "Dumbbell Bicep Curl",                 "sets": 3, "rep_range": "10-12"},
            {"name": "Tricep Overhead Extension",           "sets": 3, "rep_range": "10-12"},
        ],
    },
    "D": {
        "name":    "Legs + Core",
        "focus":   "quads, hamstrings, glutes, calves, core",
        "warmup":  "5 min treadmill incline walk + bodyweight squats 2x15",
        "exercises": [
            {"name": "Goblet Squat",                        "sets": 4, "rep_range": "10-12"},
            {"name": "Romanian Deadlift",                   "sets": 3, "rep_range": "10-12"},
            {"name": "Dumbbell Reverse Lunge",              "sets": 3, "rep_range": "10 each"},
            {"name": "Hip Thrust (shoulders on bench)",     "sets": 3, "rep_range": "12-15"},
            {"name": "Dumbbell Step-Up (on bench)",         "sets": 3, "rep_range": "10 each"},
            {"name": "Calf Raises",                         "sets": 3, "rep_range": "15-20"},
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

# ── Workout log (MongoDB) ─────────────────────────────────────────────────────
def load_log() -> dict:
    doc = _col("workout_log").find_one({"_id": "log"})
    if doc:
        doc.pop("_id", None)
        return doc
    return {"sessions": []}


def save_session(log: dict, session_data: dict) -> None:
    if not session_data.get("date") or session_data["date"] == "YYYY-MM-DD":
        session_data["date"] = date.today().isoformat()
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


# ── Memory (MongoDB) ──────────────────────────────────────────────────────────
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
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


def try_parse_profile_update(text: str) -> dict | None:
    match = re.search(r"<SAVE_PROFILE>\s*(\{.*?\})\s*</SAVE_PROFILE>", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


# ── Conversation history (MongoDB) ────────────────────────────────────────────
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


# ── Weight trend helpers ──────────────────────────────────────────────────────
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
            f"{trend} {abs(delta):.1f} kg since {first_date}")


def is_first_session_this_week(log: dict) -> bool:
    """True if no session has been logged in the current calendar week (Mon-Sun)."""
    sessions = log.get("sessions", [])
    if not sessions:
        return True
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    for s in sessions:
        try:
            d = datetime.strptime(s["date"], "%Y-%m-%d").date()
            if d >= week_start:
                return False
        except (ValueError, KeyError):
            pass
    return True


def days_since_last_session(log: dict) -> int | None:
    """Returns days since the last logged session, or None if no sessions."""
    sessions = log.get("sessions", [])
    if not sessions:
        return None
    try:
        last_date = datetime.strptime(sessions[-1]["date"], "%Y-%m-%d").date()
        return (date.today() - last_date).days
    except (ValueError, KeyError):
        return None


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


# ── Prompt builders ───────────────────────────────────────────────────────────
ONBOARDING_PROMPT = """You are a friendly personal trainer and nutrition coach AI.
A new user has just opened the app for the first time and has NO profile set up yet.

Your ONLY job right now is to collect their profile through friendly conversation.
Ask ONE question at a time, in this order:
1. Their name
2. Age
3. Current weight in kg
4. Height in cm
5. Primary goal (lose fat / build muscle / body recomposition)
6. Confirm their fitness level (beginner / some experience / intermediate)
7. Confirm: 4 days per week training (or ask if different)
8. Confirm: vegetarian Indian diet (or ask about diet)
9. Any injuries or body parts to avoid?
10. Have they been working out recently? (yes / no / used to but stopped)
    - If yes or used to: ask which exercises they were doing and roughly what dumbbell weights they were using.
      Map their answer to the closest available weights: 4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20, 22, 24 kg.
      Save these as starting weights in recent_weights.
    - If no (complete beginner to weights): set recent_weights as empty, coach will start them light.

Once you have ALL answers, output this hidden block (do not display to user):
<SAVE_PROFILE>
{
  "name": "...",
  "age": 0,
  "weight_kg": 0.0,
  "height_cm": 0.0,
  "goal": "...",
  "level": "...",
  "days_per_week": 4,
  "diet": "vegetarian Indian",
  "session_min": "45-60",
  "activity_level": "sedentary",
  "injuries": "none",
  "recent_weights": {
    "Dumbbell Flat Bench Press": 0,
    "Dumbbell Bent-Over Row": 0,
    "Goblet Squat": 0,
    "Dumbbell Overhead Press": 0,
    "Dumbbell Bicep Curl": 0
  }
}
</SAVE_PROFILE>

Fill recent_weights with the closest available dumbbell weights based on what they told you.
If they are a complete beginner with no recent training, set all weights to 0 (coach will guide them live).

Then immediately greet them warmly, show their calorie target, protein target, and tell them the 4-day split (A: Chest+Triceps, B: Back+Biceps, C: Shoulders+Arms, D: Legs+Core). Tell them to tap "Today's Workout" to begin.

Equipment available: adjustable dumbbells (4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20, 22, 24 kg), incline-decline bench, treadmill, resistance bands.
Keep messages short, warm, and encouraging. Mobile-friendly plain text only.
"""


def build_onboarding_prompt() -> str:
    return ONBOARDING_PROMPT


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


def build_system_prompt(day: str, last_session: dict | None, log: dict, mem: dict, profile: dict) -> str:
    targets = compute_targets(profile)
    cal_target = get_adjusted_calorie_target(mem, targets["calorie_target"])
    sessions = len(log.get("sessions", []))
    injuries = profile.get("injuries", "none")
    first_this_week = is_first_session_this_week(log)
    gap_days = days_since_last_session(log)
    long_gap = gap_days is not None and gap_days >= 7
    today_str = date.today().isoformat()
    recent_weights = profile.get("recent_weights", {})

    return f"""You are a personal trainer and nutrition coach AI for {profile['name']}.
You run as a web chat and Discord bot so keep replies concise and mobile-friendly.
Use plain text only, no markdown symbols.
TODAY'S DATE: {today_str} — always use this exact date when logging weights or sessions. Never guess or invent a date.

USER PROFILE:
  Name: {profile['name']} | Age: {profile['age']} | Weight: {profile['weight_kg']} kg | Height: {profile['height_cm']} cm
  Goal: {profile['goal']} | Level: {profile['level']} | Days/week: {profile['days_per_week']}
  Diet: {profile['diet']} | Session: {profile['session_min']} min | Activity outside gym: {profile.get('activity_level','sedentary')}
  Injuries: {injuries}
  Equipment: adjustable dumbbells, incline-decline bench, treadmill, resistance bands
  Available dumbbell weights (kg): 4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20, 22, 24
  IMPORTANT: Always recommend weights from the above list only. Never suggest a weight not in this list.
  When progressive overload calls for an increase, pick the next available weight up from the list.
  Calorie target (auto-adjusted): {cal_target} kcal/day
  Protein target: {targets['protein_target_g']} g/day
  Sessions logged so far: {sessions}
  Starting weights (use silently when recommending weights for first session — do NOT mention these field names to the user): {recent_weights}

{format_memory_block(mem)}

{format_program_block(day, last_session)}

YOUR RESPONSIBILITIES:

WEEKLY WEIGH-IN (first_this_week={first_this_week}):
- Ask weight ONLY if first_this_week is True (first session of this calendar week).
- If False, skip weight question entirely — do not mention it.
- When you do ask: compare to last recorded and comment on weekly pace.
- Gaining >0.5 kg/week: suggest trimming 200 kcal
- No change for 2+ weeks: suggest adding 200 kcal
- 0.1-0.5 kg/week: "perfect pace"
- Log in UPDATE_MEMORY weight_log as "{today_str}: XX.X kg" — always use today's date exactly.

MISSED WORKOUT DETECTION (long_gap={long_gap}, gap_days={gap_days}):
- If long_gap is True: warmly acknowledge the break in one sentence (no guilt-tripping).
- Then tell the user to use 10-15% lighter weights than their last session for today.
- Resume normal progressive overload from next session onward.

WORKOUT:
- Show today's workout with exercise list, sets, reps.
- Reference past soreness or form cues from memory.
- Suggest warm-up sets before heavy lifts.
- Answer form questions concisely.
- Beginner tip: start with lighter weights to learn the movement.

NUTRITION (ask after workout or when user asks):
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
    Peanuts 30g: 170 kcal, 7g protein
    Soya chunks 50g dry: 180 kcal, 26g protein
- Ask smartwatch calories burnt if they have one.
- Show summary: eaten vs {cal_target} kcal, protein vs {targets['protein_target_g']}g, net calories.
- Suggest 1-2 specific Indian dishes to close protein gap.
  Options: paneer bhurji 150g=30g protein, rajma chawal=16g,
  moong chilla 2pcs=14g, curd+sprouts=14g, milk+peanut butter shake=20g,
  soya chunks sabzi=26g, tofu bhurji=16g.

LOGGING RULES - CRITICAL:
- ONLY output LOG_SESSION when the user explicitly confirms they FINISHED the workout (e.g. "done", "finished", "completed", "logged it").
- NEVER log if the user says "will do tomorrow", "skipping today", "not today", or anything that means they did NOT do it yet.
- NEVER log nutrition unless the user actually told you what they ate today.
- If in doubt, ask "Did you complete today's workout?" before logging.
- The current day is {day}. Only log sessions for day {day} unless the user clearly states they did a different day.

LOGGING - output BOTH blocks only after confirmed completion (hidden from user):

<LOG_SESSION>
{{
  "day": "{day}",
  "date": "{today_str}",
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
            return json.loads(match.group(1))
        except Exception:
            return None
    return None
