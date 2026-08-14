"""
Progression intelligence: weekly training volume, plateau detection,
weak-point spotting, and exercise-swap suggestions.
"""

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta

from agent_core import AVAILABLE_DUMBBELLS, _col, _num, load_log, today

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


def epley_1rm(weight: float, reps: float) -> float:
    """Estimated 1-rep max (Epley). Captures BOTH heavier weight and more reps
    as progress, so adding reps at the same weight counts as an improvement."""
    w, r = _num(weight), _num(reps)
    return w * (1 + r / 30.0) if w > 0 and r > 0 else 0.0


def _exercise_best_and_volume(e: dict) -> tuple[float, float, float, float]:
    """For one logged exercise: (best_e1rm, best_weight, best_reps, total_volume).
    Uses per-set detail when present (workout mode), else the summary set."""
    sets = e.get("sets")
    if isinstance(sets, list) and sets:
        best_w, best_r, best_e1, vol = 0.0, 0.0, 0.0, 0.0
        for s in sets:
            w, r = _num(s.get("weight")), _num(s.get("reps"))
            vol += w * r
            e1 = epley_1rm(w, r)
            if e1 > best_e1:
                best_e1, best_w, best_r = e1, w, r
        return best_e1, best_w, best_r, vol
    w, r = _num(e.get("weight")), _num(e.get("reps_done"))
    return epley_1rm(w, r), w, r, w * r


def _plateau_series(log: dict) -> dict[str, list[dict]]:
    """Per exercise, one entry per session it appeared in, capturing best e1RM,
    the top set, and the total volume for that exercise that session."""
    by_ex: dict[str, list[dict]] = defaultdict(list)
    for s in log.get("sessions", []):
        per: dict[str, dict] = {}
        for e in s.get("exercises", []):
            name = e.get("name")
            if not name:
                continue
            e1, w, r, vol = _exercise_best_and_volume(e)
            cur = per.get(name)
            if cur is None:
                per[name] = {"e1rm": e1, "weight": w, "reps": r, "vol": vol}
            else:                       # same exercise logged twice in a session
                cur["vol"] += vol
                if e1 > cur["e1rm"]:
                    cur.update(e1rm=e1, weight=w, reps=r)
        for name, d in per.items():
            if d["weight"] > 0:
                by_ex[name].append(d)
    return by_ex


def _is_plateau(series: list[dict], lookback: int) -> bool:
    """A plateau only if NEITHER estimated-1RM (weight or reps) NOR volume
    (sets) improved across the window — so more reps or more sets clears it."""
    if len(series) < lookback:
        return False
    window = series[-lookback:]
    eps = 1e-6
    e1_improved = window[-1]["e1rm"] > window[0]["e1rm"] + eps
    vol_improved = window[-1]["vol"] > window[0]["vol"] + eps
    return not (e1_improved or vol_improved)


def detect_plateaus(log: dict | None = None, lookback: int = 3) -> list[str]:
    """Human-readable plateau lines: exercises with no strength (weight/reps)
    OR volume (sets) gain over the last `lookback` sessions they appeared in."""
    log = log or load_log()
    plateaus = []
    for name, series in _plateau_series(log).items():
        if _is_plateau(series, lookback):
            last = series[-1]
            plateaus.append(f"{name} (no strength or volume gain in {lookback} "
                            f"sessions — top set {last['weight']:g}kg x {last['reps']:g})")
    return plateaus


def detect_plateau_exercise_names(log: dict | None = None, lookback: int = 3) -> list[str]:
    """Bare exercise names currently plateaued (for autonomous deload flagging)."""
    log = log or load_log()
    return [name for name, series in _plateau_series(log).items()
            if _is_plateau(series, lookback)]


# ── Progressive-overload suggestion (double progression) ──────────────────────
def _rep_bounds(rep_range) -> tuple[int, int]:
    """(bottom, top) rep targets from '8-12', '10 each', '15', '15-20'."""
    nums = re.findall(r"\d+", str(rep_range))
    if not nums:
        return (8, 12)
    if len(nums) == 1:
        return (int(nums[0]), int(nums[0]))
    return (int(nums[0]), int(nums[1]))


def next_dumbbell_up(weight: float) -> float | None:
    for a in AVAILABLE_DUMBBELLS:
        if a > _num(weight) + 1e-9:
            return a
    return None                          # already at the heaviest dumbbell


def suggest_next(rep_range, last_weight, last_reps, deload: bool = False) -> dict | None:
    """Double progression: keep the weight and add reps until the top of the
    range, then move up a dumbbell and reset to the bottom of the range.
    Returns {weight, target_reps, reason, kind} or None when there's no history."""
    lw = _num(last_weight)
    if lw <= 0:
        return None
    bottom, top = _rep_bounds(rep_range)
    if deload:
        target = lw * 0.9
        lighter = [a for a in AVAILABLE_DUMBBELLS if a <= target] or [AVAILABLE_DUMBBELLS[0]]
        return {"weight": lighter[-1], "target_reps": top, "kind": "deload",
                "reason": f"scheduled deload — ~10% lighter than {lw:g}kg"}
    lr = _num(last_reps)
    if lr >= top:
        up = next_dumbbell_up(lw)
        if up:
            return {"weight": up, "target_reps": bottom, "kind": "weight_up",
                    "reason": f"↑ up to {up:g}kg — you hit {lr:g} reps at {lw:g}kg last time"}
        return {"weight": lw, "target_reps": top, "kind": "max",
                "reason": "already at your heaviest dumbbell — add reps or a set"}
    if lr > 0:
        return {"weight": lw, "target_reps": min(lr + 1, top), "kind": "rep_up",
                "reason": f"same {lw:g}kg — aim for {int(lr) + 1}+ reps (last {lr:g})"}
    return {"weight": lw, "target_reps": bottom, "kind": "same",
            "reason": f"around {lw:g}kg"}


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
