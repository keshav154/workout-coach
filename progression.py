"""
Progression intelligence: weekly training volume, plateau detection,
weak-point spotting, and exercise-swap suggestions.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from agent_core import PROGRAM, _col, _num, load_log, today

log = logging.getLogger(__name__)

# Sensible home-gym alternatives for when a movement bothers the user.
EXERCISE_ALTERNATIVES = {
    "Dumbbell Flat Bench Press":   ["Push-Ups", "Dumbbell Floor Press", "Dumbbell Incline Bench Press"],
    "Dumbbell Incline Bench Press": ["Dumbbell Flat Bench Press", "Incline Push-Ups"],
    "Dumbbell Overhead Press":     ["Dumbbell Arnold Press", "Seated Dumbbell Press", "Pike Push-Ups"],
    "Dumbbell Bent-Over Row":      ["Dumbbell Single-Arm Row", "Chest-Supported Row (on incline bench)"],
    "Goblet Squat":                ["Bulgarian Split Squat (bench)", "Dumbbell Reverse Lunge", "Box Squat"],
    "Romanian Deadlift":           ["Dumbbell Good Morning", "Single-Leg RDL", "Hip Thrust (shoulders on bench)"],
    "Bulgarian Split Squat (bench)": ["Dumbbell Reverse Lunge", "Goblet Squat", "Dumbbell Step-Up (on bench)"],
    "Dumbbell Skull Crusher (bench)": ["Tricep Overhead Extension", "Dumbbell Kickback"],
    "Dumbbell Lateral Raise":      ["Dumbbell Upright Row", "Dumbbell Front Raise"],
}


def alternatives_for(name: str) -> list[str]:
    name_l = name.lower().strip()
    for k, v in EXERCISE_ALTERNATIVES.items():
        if name_l in k.lower() or k.lower() in name_l:
            return v
    return []


def session_volume(session: dict) -> float:
    """Total tonnage for a session = sum(weight * reps) across all sets.
    Uses per-set detail when logged (workout mode); falls back to the
    summary weight x reps otherwise."""
    total = 0.0
    for e in session.get("exercises", []):
        sets = e.get("sets")
        if isinstance(sets, list) and sets:
            for s in sets:
                total += _num(s.get("weight")) * _num(s.get("reps"))
        else:
            total += _num(e.get("weight")) * _num(e.get("reps_done"))
    return total


def weekly_volume(log: dict | None = None) -> dict:
    """Tonnage for the current calendar week, plus the prior week for comparison."""
    log = log or load_log()
    now = today()
    wk_start = now - timedelta(days=now.weekday())
    this_wk, last_wk = 0.0, 0.0
    for s in log.get("sessions", []):
        try:
            d = datetime.strptime(s.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= wk_start:
            this_wk += session_volume(s)
        elif d >= wk_start - timedelta(days=7):
            last_wk += session_volume(s)
    return {"this_week": round(this_wk), "last_week": round(last_wk)}


def _plateau_exercise_weights(log: dict, lookback: int) -> dict[str, list[float]]:
    by_ex: dict[str, list[float]] = defaultdict(list)
    for s in log.get("sessions", []):
        seen = {}
        for e in s.get("exercises", []):
            name = e.get("name")
            if not name:
                continue
            seen[name] = max(seen.get(name, 0), _num(e.get("weight")))
        for name, w in seen.items():
            if w > 0:
                by_ex[name].append(w)
    return by_ex


def detect_plateaus(log: dict | None = None, lookback: int = 3) -> list[str]:
    """Human-readable plateau lines: exercises whose top working weight hasn't
    increased over the last `lookback` sessions in which they appeared."""
    log = log or load_log()
    by_ex = _plateau_exercise_weights(log, lookback)
    plateaus = []
    for name, weights in by_ex.items():
        if len(weights) >= lookback and weights[-lookback:][-1] <= weights[-lookback:][0]:
            plateaus.append(f"{name} (stuck at {weights[-1]:g}kg for {lookback} sessions)")
    return plateaus


def detect_plateau_exercise_names(log: dict | None = None, lookback: int = 3) -> list[str]:
    """Bare exercise names currently plateaued (for autonomous deload flagging)."""
    log = log or load_log()
    by_ex = _plateau_exercise_weights(log, lookback)
    return [name for name, weights in by_ex.items()
            if len(weights) >= lookback and weights[-lookback:][-1] <= weights[-lookback:][0]]


# ── Autonomous plateau intervention (auto-deload flags) ───────────────────────
def set_autodeload_flags(names: list[str]) -> list[str]:
    """Flag exercises for an automatic 10% deload on their next occurrence.
    Returns the names newly flagged (skips ones already pending)."""
    newly = []
    for name in names:
        existing = _col("auto_flags").find_one({"_id": name})
        if not existing:
            _col("auto_flags").insert_one({"_id": name, "kind": "deload"})
            newly.append(name)
    return newly


def get_autodeload_flags() -> list[str]:
    return [d["_id"] for d in _col("auto_flags").find({"kind": "deload"})]


def clear_autodeload_flag(name: str) -> None:
    _col("auto_flags").delete_one({"_id": name})


def format_autodeload_block() -> str:
    flags = get_autodeload_flags()
    if not flags:
        return ""
    return ("AUTO-DELOAD SCHEDULED (the system already decided this, don't ask permission): "
            f"{', '.join(flags)} — reduce weight ~10% (round to the nearest available dumbbell) "
            "for these specific exercises THIS session, and briefly explain it's a scheduled "
            "deload because they'd plateaued. After this session these lifts resume normal "
            "progressive overload.")


def format_progression_block(log: dict | None = None) -> str:
    log = log or load_log()
    vol = weekly_volume(log)
    plateaus = detect_plateaus(log)
    lines = []
    if vol["this_week"] or vol["last_week"]:
        trend = ""
        if vol["last_week"]:
            diff = vol["this_week"] - vol["last_week"]
            trend = f" (last week {vol['last_week']:,}, {'+' if diff >= 0 else ''}{diff:,})"
        lines.append(f"Training volume this week: {vol['this_week']:,} kg total{trend}")
    if plateaus:
        lines.append("PLATEAUS to address: " + "; ".join(plateaus))
    if not lines:
        return ""
    return "PROGRESSION:\n" + "\n".join(f"- {l}" for l in lines)


def progress_summary() -> str:
    """Human-readable !progress command output."""
    log = load_log()
    vol = weekly_volume(log)
    plateaus = detect_plateaus(log)
    out = ["Progress report", ""]
    out.append(f"Volume this week: {vol['this_week']:,} kg")
    out.append(f"Volume last week: {vol['last_week']:,} kg")
    if vol["last_week"]:
        diff = vol["this_week"] - vol["last_week"]
        out.append(f"Change: {'+' if diff >= 0 else ''}{diff:,} kg")
    out.append("")
    if plateaus:
        out.append("Plateaus (no weight increase in 3 sessions):")
        for p in plateaus:
            out.append(f"  - {p}")
        out.append("")
        out.append("Tip: deload that lift 10% and build back, or swap the variation.")
    else:
        out.append("No plateaus detected — progressing well!")
    return "\n".join(out)
