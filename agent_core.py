"""
All workout logic — program definition, MongoDB storage, system prompt builder.
Profile is stored in MongoDB; onboarding collects it on first run via chat.
"""

import copy
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

def seed_default_habit() -> None:
    """Called once, right after onboarding completes. Logging meals is the
    single highest-leverage habit for the nutrition features to pay off, so
    it starts pre-tracked on the Progress tab instead of requiring the user
    to discover and set it up themselves. $setOnInsert keeps this idempotent
    if onboarding ever runs again (e.g. after a profile reset)."""
    _col("habits").update_one(
        {"_id": "Log meals"},
        {"$setOnInsert": {"created": today_iso()}},
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
    # Fat ~25% of calories (9 kcal/g); carbs fill the remainder (4 kcal/g).
    fat_g = max(0, round(cal_target * 0.25 / 9))
    carb_g = max(0, round((cal_target - protein_g * 4 - fat_g * 9) / 4))
    return {
        "tdee": tdee,
        "calorie_target": cal_target,
        "protein_target_g": protein_g,
        "fat_target_g": fat_g,
        "carb_target_g": carb_g,
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

# The hardcoded PROGRAM above is the DEFAULT; a user can replace it with a
# custom program (stored in the 'program' collection) via the program builder.
DEFAULT_PROGRAM = PROGRAM
DAY_IDS = ["A", "B", "C", "D", "E", "F", "G"]     # up to a 7-day custom program


def get_program() -> dict:
    """The active program: the user's custom one if saved, else the default.
    Shape matches DEFAULT_PROGRAM: {day_id: {name, focus, warmup, exercises}}."""
    try:
        doc = _col("program").find_one({"_id": "active"})
    except Exception:
        doc = None
    days = (doc or {}).get("days")
    if isinstance(days, list) and days:
        prog = {}
        for d in days:
            did = d.get("id")
            if did:
                prog[did] = {"name": d.get("name", ""), "focus": d.get("focus", ""),
                             "warmup": d.get("warmup", ""),
                             "exercises": d.get("exercises", [])}
        if prog:
            return prog
    return DEFAULT_PROGRAM


def get_rotation() -> list[str]:
    """Ordered day ids of the active program (its rotation)."""
    return list(get_program().keys()) or DAY_ROTATION


def program_is_custom() -> bool:
    try:
        doc = _col("program").find_one({"_id": "active"})
    except Exception:
        return False
    return bool(doc and isinstance(doc.get("days"), list) and doc["days"])


def save_program(days: list[dict]) -> tuple[bool, str]:
    """Validate and store a custom program. `days` is an ordered list of
    {name, focus, warmup, exercises:[{name, sets, rep_range}]}; ids are
    assigned by position (A, B, C, ...)."""
    if not isinstance(days, list) or not (1 <= len(days) <= 7):
        return False, "A program needs between 1 and 7 days."
    cleaned = []
    for i, d in enumerate(days):
        exs_in = d.get("exercises") or []
        exs = []
        for e in exs_in:
            name = str(e.get("name") or "").strip()[:60]
            if not name:
                continue
            try:
                sets = int(e.get("sets") or 3)
            except (TypeError, ValueError):
                sets = 3
            sets = max(1, min(10, sets))
            rep_range = str(e.get("rep_range") or "8-12").strip()[:15] or "8-12"
            exs.append({"name": name, "sets": sets, "rep_range": rep_range})
        if not exs:
            return False, f"Day {i + 1} needs at least one exercise."
        if len(exs) > 12:
            return False, f"Day {i + 1} has too many exercises (max 12)."
        cleaned.append({
            "id":       DAY_IDS[i],
            "name":     str(d.get("name") or f"Day {DAY_IDS[i]}").strip()[:40],
            "focus":    str(d.get("focus") or "").strip()[:80],
            "warmup":   str(d.get("warmup") or "5 min light cardio, then warm-up sets").strip()[:200],
            "exercises": exs,
        })
    _col("program").update_one({"_id": "active"}, {"$set": {"days": cleaned}}, upsert=True)
    return True, "saved"


def reset_program() -> None:
    _col("program").delete_one({"_id": "active"})


def exercise_library() -> list[str]:
    """Suggested exercise names for the builder's picker — every movement in
    the default program plus its known alternatives, de-duplicated."""
    names = set()
    for d in DEFAULT_PROGRAM.values():
        for e in d.get("exercises", []):
            names.add(e["name"])
    try:
        from progression import EXERCISE_ALTERNATIVES
        for k, alts in EXERCISE_ALTERNATIVES.items():
            names.add(k)
            names.update(alts)
    except Exception:
        pass
    return sorted(names)


AVAILABLE_DUMBBELLS = [4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20.5, 22, 24]


def warmup_weight_for(working_kg) -> float | None:
    """Suggested warm-up dumbbell: ~55% of the working weight, rounded down to
    an available dumbbell. None when the working weight is light enough that a
    dedicated warm-up set adds nothing."""
    try:
        w = float(working_kg)
    except (TypeError, ValueError):
        return None
    if w < 9:
        return None
    candidates = [a for a in AVAILABLE_DUMBBELLS if a <= w * 0.55]
    return candidates[-1] if candidates else AVAILABLE_DUMBBELLS[0]

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
    rotation = get_rotation()
    if not rotation:
        return "A"
    sessions = log.get("sessions", [])
    if not sessions:
        return rotation[0]
    # Use the day of the most recent session BY DATE (robust to out-of-order
    # inserts). Among sessions sharing the latest date, take the last logged.
    max_date = max((s.get("date", "") for s in sessions), default="")
    last_day = None
    for s in sessions:
        if s.get("date", "") == max_date and s.get("day") in rotation:
            last_day = s["day"]
    if last_day not in rotation:
        last_day = sessions[-1].get("day")
    # If the last day no longer exists in the (possibly edited) program, start
    # the cycle over rather than crash.
    if last_day not in rotation:
        return rotation[0]
    idx = rotation.index(last_day)
    return rotation[(idx + 1) % len(rotation)]


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
    # deepcopy — dict(DEFAULT_MEMORY) would share the (mutable) list values
    # with the module-level default, so a later append() anywhere would
    # silently poison DEFAULT_MEMORY for the rest of the process.
    return copy.deepcopy(DEFAULT_MEMORY)


def save_memory(mem: dict) -> None:
    _col("memory").update_one(
        {"_id": "mem"},
        {"$set": mem},
        upsert=True,
    )


def _dedup_key(item):
    """Hashable key for dedup — plain items hash themselves; dicts (e.g.
    structured weight_log entries) hash their sorted JSON form."""
    return json.dumps(item, sort_keys=True) if isinstance(item, dict) else item


def apply_memory_update(mem: dict, update: dict) -> None:
    for key, new_items in update.items():
        if key in mem and isinstance(new_items, list):
            existing = {_dedup_key(x) for x in mem[key]}
            for item in new_items:
                k = _dedup_key(item)
                if k not in existing:
                    mem[key].append(item)
                    existing.add(k)


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
    # last_active lets the evening episode summary skip sources that haven't
    # talked today (message dicts themselves stay clean for the LLM API).
    _col("history").update_one(
        {"_id": source},
        {"$set": {"messages": history[-20:], "last_active": today_iso()}},
        upsert=True,
    )


def reset_history(source: str) -> None:
    _col("history").update_one(
        {"_id": source},
        {"$set": {"messages": []}},
        upsert=True,
    )


# ── Weight trend helpers ──────────────────────────────────────────────────────
def get_weight_entries(mem: dict | None = None) -> list[tuple[str, float]]:
    """THE canonical weight_log parser — every consumer goes through here.
    Tolerates legacy string entries ('YYYY-MM-DD: 97.5 kg', with or without
    trailing text) and dict entries ({'date', 'kg'}). Returns [(date_iso, kg)]
    sorted by date; malformed entries are skipped, never propagated."""
    if mem is None:
        mem = load_memory()
    out = []
    for e in mem.get("weight_log", []):
        if isinstance(e, dict):
            try:
                d, w = str(e.get("date", "")), float(e.get("kg"))
            except (TypeError, ValueError):
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                out.append((d, w))
        else:
            m = re.match(r"^(\d{4}-\d{2}-\d{2}):\s*([\d.]+)", str(e))
            if m:
                try:
                    out.append((m.group(1), float(m.group(2))))
                except ValueError:
                    pass
    out.sort(key=lambda x: x[0])
    return out


def get_weight_trend(mem: dict) -> str:
    parsed = get_weight_entries(mem)
    if not parsed:
        return "no weight history yet"
    latest_date, latest_kg = parsed[-1]
    if len(parsed) == 1:
        return f"last recorded: {latest_kg} kg on {latest_date}"
    first_date, first_kg = parsed[0]
    delta = latest_kg - first_kg
    trend = "gained" if delta > 0 else "lost"
    return (f"last: {latest_kg} kg on {latest_date} | "
            f"{trend} {abs(delta):.1f} kg since {first_date}")


# ── Rest days ─────────────────────────────────────────────────────────────────
def mark_rest_day(date_str: str | None = None) -> str:
    """Record a deliberate rest day. Returns the date marked."""
    d = date_str or today_iso()
    _col("rest_days").update_one({"_id": d}, {"$set": {"date": d}}, upsert=True)
    return d


def unmark_rest_day(date_str: str | None = None) -> None:
    _col("rest_days").delete_one({"_id": date_str or today_iso()})


def is_rest_day(date_str: str | None = None) -> bool:
    return _col("rest_days").find_one({"_id": date_str or today_iso()}) is not None


def get_consecutive_workout_days(log: dict) -> int:
    """Count consecutive training days ending today or yesterday. Deliberate
    rest days don't count as training but also don't BREAK the streak — a
    planned rest between cycles shouldn't wipe your run."""
    dates: set = set()
    for s in log.get("sessions", []):
        try:
            dates.add(datetime.strptime(s.get("date", ""), "%Y-%m-%d").date())
        except (ValueError, TypeError):
            pass
    rest: set = set()
    try:
        for r in _col("rest_days").find():
            try:
                rest.add(datetime.strptime(r.get("_id", ""), "%Y-%m-%d").date())
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    if not dates:
        return 0
    now = today()
    # Anchor on the most recent training day at today/yesterday; a rest day
    # today or yesterday is also an acceptable anchor (streak continues through
    # the planned rest).
    if now in dates or now in rest:
        anchor = now
    elif (now - timedelta(days=1)) in dates or (now - timedelta(days=1)) in rest:
        anchor = now - timedelta(days=1)
    else:
        return 0
    streak, d = 0, anchor
    while d in dates or d in rest:
        if d in dates:            # rest days bridge the streak but don't add to it
            streak += 1
        d -= timedelta(days=1)
    return streak


def get_consistent_weeks(log: dict, days_per_week: int) -> int:
    """Consecutive calendar weeks (ending this week or last) with at least
    `days_per_week` distinct training days — a kinder consistency measure than
    the day streak, which resets on any rest day."""
    from collections import defaultdict
    weeks: dict[str, set] = defaultdict(set)
    for s in log.get("sessions", []):
        try:
            d = datetime.strptime(s.get("date", ""), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        weeks[(d - timedelta(days=d.weekday())).isoformat()].add(d)
    target = max(1, int(days_per_week))
    wk = today() - timedelta(days=today().weekday())
    streak = 0
    if len(weeks.get(wk.isoformat(), ())) >= target:
        streak = 1                      # current week already complete
    wk -= timedelta(days=7)
    while len(weeks.get(wk.isoformat(), ())) >= target:
        streak += 1
        wk -= timedelta(days=7)
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
    """Returns days since the latest-dated session (robust to out-of-order
    entries), or None if no sessions."""
    dates = []
    for s in log.get("sessions", []):
        try:
            dates.append(datetime.strptime(s.get("date", ""), "%Y-%m-%d").date())
        except (ValueError, TypeError):
            pass
    if not dates:
        return None
    return (today() - max(dates)).days


def effective_calorie_target(profile: dict, targets: dict | None = None) -> int:
    """Base calorie target plus the cumulative weekly auto-adjustment the
    Sunday cron writes back to the profile (reports.auto_adjust_calories).
    Clamped so a runaway loop can never push the target off a cliff."""
    targets = targets or compute_targets(profile)
    try:
        adj = int(profile.get("cal_adjust", 0) or 0)
    except (TypeError, ValueError):
        adj = 0
    return targets["calorie_target"] + max(-600, min(600, adj))


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
      Map their answer to the closest available weights: 4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20.5, 22, 24 kg.
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

Equipment available: adjustable dumbbells (4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20.5, 22, 24 kg), incline-decline bench, treadmill.
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
    p = get_program().get(day) or DEFAULT_PROGRAM.get(day, {"name": "", "focus": "",
                                                           "warmup": "", "exercises": []})
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
    p_name = get_program().get(day, {}).get("name", "")
    cal_target = effective_calorie_target(profile, targets)
    sessions = len(log.get("sessions", []))
    injuries = profile.get("injuries", "none")
    first_this_week = is_first_session_this_week(log)
    gap_days = days_since_last_session(log)
    long_gap = gap_days is not None and gap_days >= 7
    recent_weights = profile.get("recent_weights", {})
    consecutive_days = get_consecutive_workout_days(log)
    suggest_deload = should_suggest_deload(log)
    cycle_len = len(get_rotation()) or 6
    exercises_done = {e["name"] for s in log.get("sessions", []) for e in s.get("exercises", [])}

    return f"""You are a personal AI assistant for {profile['name']} — their fitness coach, nutrition coach, AND personal finance/expense tracker, all in one.
You see the full conversation history every turn, so always interpret each message in the context of what you just asked and what the user is responding to.
You run as a web/Telegram/Discord chat so keep replies concise and mobile-friendly.
Use plain text only, no markdown symbols.

AUTHORITATIVE FACTS (set by the system — these are TRUE, do not contradict or recompute them):
- TODAY'S DATE is {today_str}. Never use any other date.
- TODAY'S TRAINING DAY is Day {day} — {p_name}. This is the correct workout for today.
- IGNORE any different day or date mentioned in earlier messages in this conversation — those were previous days. If the user asks what's today's workout, it is ALWAYS Day {day} ({p_name}), never a day from an earlier message.

TOOLS ARE HOW YOU ACT — you have READ tools and WRITE tools.
READ tools (query_today_workout, query_workouts, query_exercise, query_weight, query_measurements, query_health, query_spending, query_profile, query_memory, generate_spending_review, get_system_status): when you need ANY fact — a past weight, a rep count, session counts, spending totals, a personal best, something from a previous day's conversation — CALL THE TOOL and treat its result as the single source of truth. Never guess a number; fetch it. If a tool result and your memory disagree, the tool is correct.
WRITE tools (log_workout_session, log_body_weight, log_body_measurement, log_meal_entry, log_expense_entry, log_water, log_health, log_cardio, save_daily_checkin, set_user_goal, clear_user_goals, set_category_budget, update_training_days, mark_rest_day, add_habit, log_habit_done, remove_habit, undo_last_action, record_memory_note, record_lesson): when the user reports something that should be saved, CALL THE MATCHING TOOL — do not merely say you logged it. The tool returns SAVED or REJECTED with details: only claim success if it returned SAVED; if REJECTED, tell the user why and ask them to confirm. One message can require several tools (e.g. finished workout + mentions weight + what they ate = log_workout_session + log_body_weight + log_meal_entry).
LEARNING: whenever the user corrects you — wrong day, misread number, wrong intent, anything — call record_lesson with what you got wrong and the generalized rule. This is mandatory, not optional.

USER PROFILE:
  Name: {profile['name']} | Age: {profile['age']} | Weight: {profile['weight_kg']} kg | Height: {profile['height_cm']} cm
  Goal: {profile['goal']} | Level: {profile['level']} | Days/week: {profile['days_per_week']}
  Diet: {profile['diet']} | Session: {profile['session_min']} min | Activity outside gym: {profile.get('activity_level','sedentary')}
  Injuries: {injuries}
  Equipment: adjustable dumbbells, incline-decline bench, treadmill (the user does NOT have resistance bands — never suggest band exercises)
  Available dumbbell weights (kg): 4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20.5, 22, 24
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
- Have you already said this exact thing earlier in this conversation? Re-read the last few assistant turns above. If you already gave the workout list / meal suggestion / summary the user is now just acknowledging ("good", "ok", "sounds good", "thanks"), do NOT repeat it — respond briefly and move the conversation forward instead. Repeating a prior message verbatim is always wrong.
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

REST DAY SUGGESTION (consecutive_days={consecutive_days}, cycle_length={cycle_len}):
- The user's program is a {cycle_len}-day cycle. The natural rest day is AFTER completing the last day of the cycle, i.e. after {cycle_len} sessions in a row.
- If consecutive_days >= {cycle_len}: recommend taking tomorrow as a full rest day before restarting the cycle at day 1.
- If consecutive_days is high relative to the cycle but recovery readiness is low, gently offer an optional rest day — but don't push it.
- Mention it briefly at the end of the workout, never as a warning.

DELOAD WEEK (suggest_deload={suggest_deload}):
- If suggest_deload is True: tell the user this is deload week — use 60% of normal weights, same sets/reps.
- Explain it helps muscles recover and come back stronger. Only mention once per session.

FORM CUES (exercises_done={exercises_done}):
- For each exercise in today's workout, if the exercise name is NOT in exercises_done (first time ever doing it), include its form cue in one line below the exercise.
- If the exercise has been done before, skip the form cue unless user asks.

WORKOUT:
- CONVERSATION STATE, READ THIS FIRST: look back through the conversation history above. If you (the assistant) already listed today's exercises earlier in THIS conversation, do NOT print the full workout again. A repeat listing is a bug, not helpfulness.
    - If the user just affirmed ("good", "sounds good", "ok", "yes", "let's go") after you already showed the workout, reply with a SHORT encouraging line only (e.g. "Great, go get it! Tell me your weights/reps as you finish each set, or let me know when you're done.") — do not restate the exercise list.
    - If the user asks a follow-up (form question, wants to swap an exercise, reports pain), answer only that, briefly — don't re-print the whole list.
    - Only present the FULL workout listing when: this is the first time it's being shown in the conversation, OR the user explicitly asks to see it again ("show me the workout again", "what's the list").
- Before presenting the workout (first time only), REASON in your hidden section (before ===REPLY===) exercise by exercise to pick a concrete recommendation for each one. Use DOUBLE PROGRESSION:
    1. Start from last session's top set for that exercise (shown in the program block above as "last: Xkg x Y reps").
    2. If they hit the TOP of the rep range last time, progress to the next available dumbbell weight up (4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20.5, 22, 24 kg) and reset to the BOTTOM of the rep range.
    3. If they did NOT yet reach the top of the range, keep the SAME weight and tell them to add 1-2 reps this time. Adding reps at the same weight is real progress — treat it as progression, not a plateau.
    4. Only if they are already at the heaviest dumbbell AND the top of the range: suggest adding a set or slowing the tempo.
    5. If no history exists, use their onboarding starting weight; if that's 0, pick a sensible beginner weight and say it's a starting estimate to adjust live.
    6. Adjust for context: long gap or low recovery readiness -> drop ~10-15% (round to an available weight); deload week -> ~60%; good recovery and consistent progress -> confident progression.
- THEN present today's workout: each exercise with sets, rep range, AND the specific recommended weight + rep target you reasoned out (only weights from the available list).
- Briefly note WHY ("up from 13.5 to 16 since you hit 12 reps" / "same 18kg, aim for 11+ reps" / "lighter today, you slept poorly").
- Reference past soreness or form cues from memory. Suggest warm-up sets before heavy lifts.
- Answer form questions concisely. Beginner tip: start lighter to learn the movement.

NUTRITION (ask after workout or when user asks):
- Ask what they ate meal by meal.
- Estimate calories AND protein AND carbs AND fat per item and pass all four to log_meal_entry (carbs_g, fat_g). Use the calorie/protein anchors below and estimate carbs/fat sensibly (e.g. roti/rice/paratha are carb-heavy; paneer/peanuts/oil are fat-heavy; dal/rajma are balanced).
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

WHEN TO SAVE — CRITICAL JUDGMENT RULES (apply to the WRITE tools):
- log_workout_session ONLY when the user clearly FINISHED training (e.g. "done", "finished"). NEVER for "will do tomorrow", "skipping today", plans, or casual chat. If in doubt, ask "Did you complete today's workout?" first.
- Interpret every message in the context of what YOU asked last. A bare number after your weight question is a body weight (log_body_weight), not an expense.
- log_meal_entry for ANY food mention, any time of day — estimate calories/protein from the Indian portion guide below if they didn't give numbers.
- save_daily_checkin whenever sleep / energy / soreness comes up; omit fields not mentioned.
- record_memory_note for durable facts worth remembering (injury, preference, form cue, pattern).
- After a WRITE tool responds, relay the outcome honestly: SAVED means confirmed; REJECTED means tell the user why and re-ask. Never say "logged" without a SAVED result.

LEGACY FALLBACK (only if tool calls aren't working this turn — hidden blocks below, parsed and stripped; prefer tools): <LOG_SESSION>{{"day": "{day}", "date": "{today_str}", "body_weight_kg": 0.0, "exercises": [{{"name": "...", "weight": 0, "reps_done": 0}}]}}</LOG_SESSION>, <LOG_MEAL>{{"description": "...", "calories": 0, "protein": 0}}</LOG_MEAL>, <LOG_EXPENSE>{{"amount": 0.0, "description": "...", "category": "..."}}</LOG_EXPENSE>, <CHECKIN>{{"sleep_hours": 7, "energy": 8, "soreness": 3}}</CHECKIN>, <SET_GOAL>{{"kind": "weight", "target": 90, "by_date": "2026-09-01"}}</SET_GOAL>, <SET_BUDGET>{{"category": "Food", "amount": 8000}}</SET_BUDGET>, <UPDATE_PROFILE>{{"days_per_week": 5}}</UPDATE_PROFILE>, <UNDO></UNDO>, <CLEAR_GOALS></CLEAR_GOALS>, <UPDATE_MEMORY>{{"weight_log": ["{today_str}: XX.X kg"], "injuries_soreness": [], "preferences": []}}</UPDATE_MEMORY>.

There is NO command syntax in this app — never tell the user to type a command. Everything happens by talking normally. If the user asks what they can do, describe it in plain sentences ("just tell me...").
For QUESTIONS about progress, plateaus, goals, spending, or recovery, answer from the data provided above or call a READ tool — never tell the user to run a command.

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
