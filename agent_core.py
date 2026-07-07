"""
All workout logic — program definition, MongoDB storage, system prompt builder.
Profile is stored in MongoDB; onboarding collects it on first run via chat.
"""

import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import certifi
from pymongo import MongoClient

# ── Local time ────────────────────────────────────────────────────────────────
# Render runs in UTC; the user is in IST (UTC+5:30). Compute "today" in the
# user's timezone so dates and the day rotation are correct near midnight.
_TZ_OFFSET_MIN = int(os.environ.get("APP_TZ_OFFSET_MIN", "330"))  # 330 = IST
_APP_TZ = timezone(timedelta(minutes=_TZ_OFFSET_MIN))

def today() -> date:
    return datetime.now(_APP_TZ).date()

def today_iso() -> str:
    return today().isoformat()

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

# ── 6-day Push/Pull/Legs x2 (each muscle trained twice per week) ─────────────
PROGRAM = {
    "A": {
        "name":    "Push (Chest focus)",
        "focus":   "chest, front/side delts, triceps",
        "warmup":  "5 min treadmill brisk walk, then 1 light set each press",
        "exercises": [
            {"name": "Dumbbell Flat Bench Press",      "sets": 4, "rep_range": "8-12",  "form": "Retract shoulder blades, lower to mid-chest, press straight up."},
            {"name": "Dumbbell Incline Bench Press",   "sets": 4, "rep_range": "8-12",  "form": "Bench at 30-45 degrees, targets upper chest."},
            {"name": "Dumbbell Flat Chest Fly",        "sets": 3, "rep_range": "10-12", "form": "Slight elbow bend, wide arc, stretch at the bottom."},
            {"name": "Dumbbell Overhead Press",        "sets": 3, "rep_range": "8-12",  "form": "Press straight up, brace core — front delts."},
            {"name": "Dumbbell Lateral Raise",         "sets": 3, "rep_range": "12-15", "form": "Lead with elbows, stop at shoulder height — side delts."},
            {"name": "Tricep Overhead Extension",      "sets": 3, "rep_range": "10-12", "form": "Elbows forward, only forearms move."},
        ],
    },
    "B": {
        "name":    "Pull (Back thickness)",
        "focus":   "lats, mid-back, traps, rear delts, biceps",
        "warmup":  "5 min treadmill brisk walk, then arm circles + bodyweight rows on bench 2x15",
        "exercises": [
            {"name": "Dumbbell Bent-Over Row",         "sets": 4, "rep_range": "8-12",  "form": "Hinge ~45 degrees, pull to hip, squeeze shoulder blades."},
            {"name": "Dumbbell Single-Arm Row",        "sets": 3, "rep_range": "8-12",  "form": "Support on bench, pull elbow past torso, flat back."},
            {"name": "Dumbbell Pullover (on bench)",   "sets": 3, "rep_range": "10-12", "form": "Arc dumbbell behind head, stretch lats, pull over — lat width."},
            {"name": "Dumbbell Shrug",                 "sets": 3, "rep_range": "12-15", "form": "Lift shoulders straight to ears, pause — traps."},
            {"name": "Dumbbell Rear Delt Fly (bent-over)", "sets": 3, "rep_range": "15", "form": "Hinge forward ~45 degrees, light dumbbells, raise out to sides, squeeze shoulder blades."},
            {"name": "Dumbbell Bicep Curl",            "sets": 3, "rep_range": "10-12", "form": "Elbows fixed at sides, full range, squeeze at top."},
        ],
    },
    "C": {
        "name":    "Legs (Quad focus)",
        "focus":   "quads, glutes, calves",
        "warmup":  "5 min treadmill incline walk + bodyweight squats 2x15",
        "exercises": [
            {"name": "Goblet Squat",                   "sets": 4, "rep_range": "10-12", "form": "Dumbbell at chest, squat deep, knees over toes, chest up."},
            {"name": "Bulgarian Split Squat (bench)",  "sets": 3, "rep_range": "10 each","form": "Rear foot on bench, drop straight down, drive through front heel."},
            {"name": "Dumbbell Reverse Lunge",         "sets": 3, "rep_range": "10 each","form": "Step back, front knee ~90 degrees, don't let it cave in."},
            {"name": "Romanian Deadlift",              "sets": 3, "rep_range": "10-12", "form": "Hinge at hips, soft knees, hamstring stretch, flat back."},
            {"name": "Hip Thrust (shoulders on bench)","sets": 3, "rep_range": "12-15", "form": "Drive through heels, squeeze glutes at top."},
            {"name": "Calf Raises",                    "sets": 4, "rep_range": "15-20", "form": "Full range — stretch at bottom, squeeze at top."},
        ],
    },
    "D": {
        "name":    "Push (Shoulder focus)",
        "focus":   "all 3 delts, upper chest, triceps",
        "warmup":  "5 min treadmill brisk walk, then arm circles + bodyweight rows on bench 2x15",
        "exercises": [
            {"name": "Dumbbell Overhead Press",        "sets": 4, "rep_range": "8-12",  "form": "Press straight up, don't over-flare elbows."},
            {"name": "Dumbbell Arnold Press",          "sets": 3, "rep_range": "10-12", "form": "Rotate palms in-to-out as you press — full delt hit."},
            {"name": "Dumbbell Lateral Raise",         "sets": 4, "rep_range": "12-15", "form": "Lead with elbows — side delts, the key to width."},
            {"name": "Dumbbell Front Raise",           "sets": 3, "rep_range": "12-15", "form": "Slight elbow bend, raise to eye level — front delts."},
            {"name": "Dumbbell Incline Bench Press",   "sets": 3, "rep_range": "8-12",  "form": "Upper-chest press to round out the push."},
            {"name": "Dumbbell Skull Crusher (bench)", "sets": 3, "rep_range": "10-12", "form": "Lower dumbbells beside head, extend, elbows tucked — triceps."},
        ],
    },
    "E": {
        "name":    "Pull (Back width + arms)",
        "focus":   "lats, rear delts, traps, biceps, forearms",
        "warmup":  "5 min treadmill brisk walk, then arm circles + bodyweight rows on bench 2x15",
        "exercises": [
            {"name": "Dumbbell Single-Arm Row",        "sets": 4, "rep_range": "8-12",  "form": "Heavy, full stretch and squeeze each rep."},
            {"name": "Dumbbell Pullover (on bench)",   "sets": 3, "rep_range": "10-12", "form": "Arc dumbbell behind head, stretch lats, pull over — lat width."},
            {"name": "Dumbbell Upright Row",           "sets": 3, "rep_range": "12-15", "form": "Pull up the body to chest height, elbows lead — traps/side delts."},
            {"name": "Dumbbell Rear Delt Fly (bent-over)", "sets": 3, "rep_range": "15", "form": "Hinge forward ~45 degrees, light dumbbells, raise out to sides, squeeze shoulder blades."},
            {"name": "Dumbbell Kickback",              "sets": 3, "rep_range": "12-15", "form": "Hinge forward, upper arm still, extend forearm back — triceps isolation."},
            {"name": "Hammer Curl",                    "sets": 4, "rep_range": "10-12", "form": "Neutral grip — biceps and forearm thickness."},
        ],
    },
    "F": {
        "name":    "Legs (Posterior focus)",
        "focus":   "hamstrings, glutes, quads, calves",
        "warmup":  "5 min treadmill incline walk + bodyweight squats 2x15",
        "exercises": [
            {"name": "Romanian Deadlift",              "sets": 4, "rep_range": "10-12", "form": "Hinge deep, feel the hamstring stretch, flat back — main hamstring lift."},
            {"name": "Hip Thrust (shoulders on bench)","sets": 4, "rep_range": "12-15", "form": "Drive through heels, hard glute squeeze at the top."},
            {"name": "Goblet Squat",                   "sets": 3, "rep_range": "10-12", "form": "Deep squat, chest up, controlled."},
            {"name": "Dumbbell Step-Up (on bench)",    "sets": 3, "rep_range": "10 each","form": "Drive through the bench-foot heel, don't push off the back foot."},
            {"name": "Dumbbell Reverse Lunge",         "sets": 3, "rep_range": "10 each","form": "Controlled step back, upright torso."},
            {"name": "Calf Raises",                    "sets": 4, "rep_range": "15-20", "form": "Full range, pause and squeeze at the top."},
        ],
    },
}

DAY_ROTATION = ["A", "B", "C", "D", "E", "F"]

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
        session_data["date"] = today_iso()
    d   = session_data["date"]
    day = session_data.get("day")

    # Idempotent: if a session for the same date AND day already exists, replace
    # it instead of appending a duplicate (prevents double-logging across
    # Telegram + web workout mode, or the model re-logging on a later turn).
    doc = _col("workout_log").find_one({"_id": "log"}) or {}
    sessions = doc.get("sessions", [])
    for i in range(len(sessions) - 1, -1, -1):
        if sessions[i].get("date") == d and sessions[i].get("day") == day:
            _col("workout_log").update_one(
                {"_id": "log"},
                {"$set": {f"sessions.{i}": session_data}},
                upsert=True,
            )
            return
    _col("workout_log").update_one(
        {"_id": "log"},
        {"$push": {"sessions": session_data}},
        upsert=True,
    )


def repair_workout_data() -> dict:
    """Clean up existing sessions: drop duplicates (same date+day, keep the
    latest), clamp future/invalid dates to today, and re-sort by date so the
    day rotation is correct. Safe to call repeatedly (idempotent)."""
    doc = _col("workout_log").find_one({"_id": "log"}) or {}
    sessions = doc.get("sessions", [])
    now = today_iso()

    fixed_dates = 0
    for s in sessions:
        d = s.get("date", "")
        try:
            if not d or datetime.strptime(d, "%Y-%m-%d").date().isoformat() > now:
                s["date"] = now; fixed_dates += 1
        except ValueError:
            s["date"] = now; fixed_dates += 1

    seen = {}
    for s in sessions:
        seen[(s.get("date"), s.get("day"))] = s
    result = sorted(seen.values(), key=lambda s: s.get("date", ""))
    removed = len(sessions) - len(result)

    if removed or fixed_dates:
        _col("workout_log").update_one({"_id": "log"}, {"$set": {"sessions": result}}, upsert=True)
    return {"removed_duplicates": removed, "fixed_dates": fixed_dates, "remaining": len(result)}


def get_next_day(log: dict) -> str:
    sessions = log.get("sessions", [])
    if not sessions:
        return "A"
    # Use the day of the most recent session BY DATE (robust to out-of-order
    # inserts). Among sessions sharing the latest date, take the last logged.
    max_date = max((s.get("date", "") for s in sessions), default="")
    last_day = None
    for s in sessions:
        if s.get("date", "") == max_date and s.get("day") in DAY_ROTATION:
            last_day = s["day"]
    if last_day not in DAY_ROTATION:
        last_day = sessions[-1].get("day", "A")
    idx = DAY_ROTATION.index(last_day)
    return DAY_ROTATION[(idx + 1) % len(DAY_ROTATION)]


def get_last_session_for_day(log: dict, day: str) -> dict | None:
    for session in reversed(log.get("sessions", [])):
        if session.get("day") == day:
            return session
    return None


# ── Personal record detection ─────────────────────────────────────────────────
def _num(v) -> float:
    """Parse a leading number out of values like '12', '10-12', '10 each', '24kg'."""
    try:
        return float(str(v).split("-")[0].split()[0].replace("kg", "").strip())
    except (ValueError, AttributeError, IndexError):
        return 0.0


def detect_prs(log: dict, new_session: dict) -> list[str]:
    """Compare a new session against all prior sessions; return PR celebration lines."""
    prev_best: dict[str, tuple[float, float]] = {}
    for s in log.get("sessions", []):
        for ex in s.get("exercises", []):
            name = ex.get("name")
            if not name:
                continue
            cur = (_num(ex.get("weight")), _num(ex.get("reps_done")))
            if name not in prev_best or cur > prev_best[name]:
                prev_best[name] = cur

    prs = []
    for ex in new_session.get("exercises", []):
        name = ex.get("name")
        w    = _num(ex.get("weight"))
        r    = _num(ex.get("reps_done"))
        if not name or w <= 0:
            continue
        best = prev_best.get(name)
        if best is None:
            continue  # first time doing it — not a PR to celebrate
        bw, br = best
        if w > bw or (w == bw and r > br):
            wtxt = f"{w:g}kg x {r:g} reps"
            btxt = f"{bw:g}kg x {br:g}"
            prs.append(f"New PR on {name}: {wtxt} (previous best {btxt})")
    return prs


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


def get_consecutive_workout_days(log: dict) -> int:
    """Count how many days in a row (by calendar date) sessions were logged."""
    sessions = log.get("sessions", [])
    if not sessions:
        return 0
    dates = sorted(set(s["date"] for s in sessions if "date" in s), reverse=True)
    streak = 0
    expected = today()
    for d in dates:
        try:
            session_date = datetime.strptime(d, "%Y-%m-%d").date()
            if session_date == expected or session_date == expected - timedelta(days=1):
                streak += 1
                expected = session_date - timedelta(days=1)
            else:
                break
        except ValueError:
            pass
    return streak


def should_suggest_deload(log: dict) -> bool:
    """Suggest deload every 24 sessions (~6 weeks of 4x/week training)."""
    sessions = log.get("sessions", [])
    count = len(sessions)
    return count > 0 and count % 24 == 0


def is_first_session_this_week(log: dict) -> bool:
    """True if no session has been logged in the current calendar week (Mon-Sun)."""
    sessions = log.get("sessions", [])
    if not sessions:
        return True
    now = today()
    week_start = now - timedelta(days=now.weekday())  # Monday
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
        return (today() - last_date).days
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
7. Confirm: 6 days per week training (Push/Pull/Legs twice) — or ask if different
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
  "days_per_week": 6,
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

Then immediately greet them warmly, show their calorie target, protein target, and tell them the 6-day Push/Pull/Legs split that trains every muscle twice a week (A: Push-chest, B: Pull-back, C: Legs-quad, D: Push-shoulders, E: Pull-width+arms, F: Legs-posterior). Tell them to tap "Today's Workout" to begin.

Equipment available: adjustable dumbbells (4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20, 22, 24 kg), incline-decline bench, treadmill.
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
        if ex.get("scheme"):
            line = f"  - {ex['name']}  |  {ex['scheme']}"
        else:
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


def build_system_prompt(day: str, last_session: dict | None, log: dict, mem: dict, profile: dict,
                        extra_context: str = "") -> str:
    targets = compute_targets(profile)
    today_str = today_iso()
    p_name = PROGRAM.get(day, {}).get("name", "")
    cal_target = get_adjusted_calorie_target(mem, targets["calorie_target"])
    sessions = len(log.get("sessions", []))
    injuries = profile.get("injuries", "none")
    first_this_week = is_first_session_this_week(log)
    gap_days = days_since_last_session(log)
    long_gap = gap_days is not None and gap_days >= 7
    recent_weights = profile.get("recent_weights", {})
    consecutive_days = get_consecutive_workout_days(log)
    suggest_deload = should_suggest_deload(log)
    exercises_done = {e["name"] for s in log.get("sessions", []) for e in s.get("exercises", [])}

    return f"""You are a personal AI assistant for {profile['name']} — their fitness coach, nutrition coach, AND personal finance/expense tracker, all in one.
You see the full conversation history every turn, so always interpret each message in the context of what you just asked and what the user is responding to.
You run as a web/Telegram/Discord chat so keep replies concise and mobile-friendly.
Use plain text only, no markdown symbols.

AUTHORITATIVE FACTS (set by the system — these are TRUE, do not contradict or recompute them):
- TODAY'S DATE is {today_str}. Never use any other date.
- TODAY'S TRAINING DAY is Day {day} — {p_name}. This is the correct workout for today.
- IGNORE any different day or date mentioned in earlier messages in this conversation — those were previous days. If the user asks what's today's workout, it is ALWAYS Day {day} ({p_name}), never a day from an earlier message.

DATA TOOLS — you can read the user's REAL data from the database with tools (query_today_workout, query_workouts, query_exercise, query_weight, query_spending, query_profile). When you need ANY fact — a past weight, a rep count, how many sessions, spending totals, a personal best — CALL THE TOOL and treat its result as the single source of truth. Never guess a number or recall it from earlier messages; fetch it. If a tool result and your memory disagree, the tool is correct.

USER PROFILE:
  Name: {profile['name']} | Age: {profile['age']} | Weight: {profile['weight_kg']} kg | Height: {profile['height_cm']} cm
  Goal: {profile['goal']} | Level: {profile['level']} | Days/week: {profile['days_per_week']}
  Diet: {profile['diet']} | Session: {profile['session_min']} min | Activity outside gym: {profile.get('activity_level','sedentary')}
  Injuries: {injuries}
  Equipment: adjustable dumbbells, incline-decline bench, treadmill (the user does NOT have resistance bands — never suggest band exercises)
  Available dumbbell weights (kg): 4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20, 22, 24
  IMPORTANT: Always recommend weights from the above list only. Never suggest a weight not in this list.
  When progressive overload calls for an increase, pick the next available weight up from the list.
  Calorie target (auto-adjusted): {cal_target} kcal/day
  Protein target: {targets['protein_target_g']} g/day
  Sessions logged so far: {sessions}
  Starting weights (use silently when recommending weights for first session — do NOT mention these field names to the user): {recent_weights}

{format_memory_block(mem)}

{extra_context}

{format_program_block(day, last_session)}

REASONING (do this before EVERY reply):
First reason privately, THEN output a line containing exactly ===REPLY=== and AFTER that line write your user-facing message. Everything before ===REPLY=== is hidden from the user and must contain ALL of your reasoning. In that hidden section, think through:
- Did the user actually COMPLETE today's workout, or are they planning/declining/asking? Only log a session if they clearly completed it. If they said "tomorrow", "later", "skipping", or are just chatting — do NOT log.
- Are the numbers they gave sane (weight, reps, calories)? Flag anything off instead of logging it.
- Given their history, available dumbbell weights, and recovery, what is the right weight/intensity to recommend?
- What is the single most useful next step or question?
CRITICAL: Never let any reasoning appear after ===REPLY===. After the marker, write only the clean message the user should see. Always include the ===REPLY=== marker.

YOUR RESPONSIBILITIES:

RECOVERY, PROGRESSION & GOALS:
- If a RECOVERY READINESS score is shown above, let it guide intensity: 8-10 push for progression or a PR; 5-7 train as planned; 1-4 back off 10-15% and briefly say why (sleep/energy/soreness).
- If PLATEAUS are listed, address them: suggest deloading that lift ~10% and rebuilding, or swapping to a variation. Mention it naturally during the session.
- If ACTIVE GOALS are shown, reference them to motivate and tie today's work to the goal and its pace.
- If the user reports pain or can't do a movement today, offer a sensible alternative that trains the same muscle with their equipment (dumbbells, bench, treadmill only — no bands).

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

REST DAY SUGGESTION (consecutive_days={consecutive_days}):
- This is a 6-day Push/Pull/Legs cycle (A-F). The natural rest day is AFTER completing Legs (Posterior) / day F, i.e. after 6 sessions in a row.
- If consecutive_days >= 6: recommend taking tomorrow as a full rest day before restarting the cycle at Push (Chest).
- If consecutive_days is 3-5 but recovery readiness is low, gently offer an optional rest day — but don't push it; the split is designed to be run on consecutive days.
- Mention it briefly at the end of the workout, never as a warning.

DELOAD WEEK (suggest_deload={suggest_deload}):
- If suggest_deload is True: tell the user this is deload week — use 60% of normal weights, same sets/reps.
- Explain it helps muscles recover and come back stronger. Only mention once per session.

FORM CUES (exercises_done={exercises_done}):
- For each exercise in today's workout, if the exercise name is NOT in exercises_done (first time ever doing it), include its form cue in one line below the exercise.
- If the exercise has been done before, skip the form cue unless user asks.

WORKOUT:
- Before presenting the workout, REASON in your hidden section (before ===REPLY===) exercise by exercise to pick a concrete recommended weight for each one:
    1. Start from last session's weight for that exercise (shown in the program block above as "last: Xkg").
    2. If they hit the TOP of the rep range last time, progress to the next available dumbbell weight up (4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20, 22, 24 kg). If they fell short, keep the same weight.
    3. If no history exists, use their onboarding starting weight; if that's 0, pick a sensible beginner weight and say it's a starting estimate to adjust live.
    4. Adjust for context: long gap or low recovery readiness -> drop ~10-15% (round to an available weight); deload week -> ~60%; good recovery and consistent progress -> confident progression.
- THEN present today's workout: each exercise with sets, rep range, AND the specific recommended weight you reasoned out (only weights from the available list).
- Briefly note WHY when you change a weight ("up from 13.5 since you hit 12 reps last time" / "lighter today, you slept poorly").
- Reference past soreness or form cues from memory. Suggest warm-up sets before heavy lifts.
- Answer form questions concisely. Beginner tip: start lighter to learn the movement.

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

EXPENSE TRACKING (you also track this user's spending):
- You handle money too. When the user clearly reports a purchase/spend (e.g. "spent 500 on groceries", "paid 200 petrol", "bought shoes for 1800"), log it.
- Use FULL CONVERSATION CONTEXT to decide intent. A bare number is NOT always money. If you just asked for their weight and they reply "97.3" or "97.3 feeling good", that is their BODY WEIGHT, not an expense. If they mention reps, sets, kg, sleep, or how they feel, it is fitness — never an expense.
- Categories: Food, Transport, Bills, Shopping, Health, Entertainment, Other.
- When logging an expense, output the hidden LOG_EXPENSE block. Do NOT write your own "Logged Rs..." confirmation — the app automatically appends one; just acknowledge naturally in a few words.

LOGGING RULES - CRITICAL:
- ONLY output LOG_SESSION when the user explicitly confirms they FINISHED the workout (e.g. "done", "finished", "completed", "logged it").
- NEVER log if the user says "will do tomorrow", "skipping today", "not today", or anything that means they did NOT do it yet.
- NEVER log nutrition unless the user actually told you what they ate today.
- If in doubt, ask "Did you complete today's workout?" before logging.
- The current day is {day}. Only log sessions for day {day} unless the user clearly states they did a different day.
- Decide what each message means from the running conversation. You asked questions earlier in this chat — interpret the user's reply as the answer to what you actually asked.

LOGGING - output the relevant hidden block(s) only when appropriate (hidden from user):

<LOG_EXPENSE>
{{"amount": 0.0, "description": "...", "category": "...", "note": ""}}
</LOG_EXPENSE>
(Use ONLY for a real purchase. Never for a body weight, rep count, or other number.)

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

NATURAL ACTIONS — the user should NEVER need to type a command. When they express any of these in plain language, emit the matching hidden block (in addition to your normal reply). Infer values from what they said; omit fields they didn't give.
- They mention sleep / energy / soreness / how recovered they feel:
  <CHECKIN>{{"sleep_hours": 7, "energy": 8, "soreness": 3}}</CHECKIN>
- They state a goal ("I want to reach 90 kg by September", "get my bench to 24"):
  <SET_GOAL>{{"kind": "weight", "target": 90, "by_date": "2026-09-01"}}</SET_GOAL>
  or for a lift: <SET_GOAL>{{"kind": "lift", "exercise": "bench", "target": 24}}</SET_GOAL>
- They ask to undo / remove / delete the last thing logged:
  <UNDO></UNDO>
- They want to change how many days per week they train:
  <UPDATE_PROFILE>{{"days_per_week": 5}}</UPDATE_PROFILE>
- They mention eating ANYTHING, at any time (not just after a workout) — log it immediately, separate from LOG_SESSION nutrition:
  <LOG_MEAL>{{"description": "...", "calories": 0, "protein": 0}}</LOG_MEAL>
  Estimate calories/protein using the Indian portion guide below. Log every meal mention, even outside a workout conversation.
- They set or change a monthly spending budget for a category ("cap my food spending at 8000 a month"):
  <SET_BUDGET>{{"category": "Food", "amount": 8000}}</SET_BUDGET>
- They ask to clear/remove/reset their goals:
  <CLEAR_GOALS></CLEAR_GOALS>
There is NO command syntax in this app — never tell the user to type a command with "!" or otherwise. Everything is done by talking normally and by you calling tools or emitting the blocks above. If the user asks what they can do, describe it in plain sentences ("just tell me...").
For QUESTIONS about progress, plateaus, goals, spending, or recovery, just ANSWER from the data already provided above, or call a tool to fetch it — never tell the user to run a command.

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
