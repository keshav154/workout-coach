"""
Weekly training volume per muscle group — the metric hypertrophy programming
actually optimises, which tonnage and per-lift plateaus don't capture.

Sets are classified to a PRIMARY muscle by keyword (so it works for custom
program lifts too, not just a fixed table), tallied for the current week, and
compared to evidence-based weekly landmarks (roughly MEV..MAV). The point is to
flag a muscle that's under-stimulated or being run into the ground.
"""

import logging
from datetime import datetime, timedelta

from agent_core import get_program, load_log, today

log = logging.getLogger(__name__)

# Weekly DIRECT working-set landmarks (low ~= MEV, high ~= MAV). Approximate,
# per common hypertrophy guidance; used only to label under/over, not as gospel.
LANDMARKS = {
    "Chest":     (10, 22),
    "Back":      (10, 22),
    "Shoulders": (8, 24),
    "Legs":      (8, 20),
    "Biceps":    (6, 18),
    "Triceps":   (6, 18),
    "Core":      (0, 20),
}

# Ordered keyword -> muscle rules. First matching rule(s) win; a lift can map to
# more than one primary mover (e.g. a deadlift hits back and legs).
_RULES = [
    ("Legs",      ("squat", "lunge", "leg ", "leg-", "split squat", "rdl",
                   "romanian", "deadlift", "hip thrust", "calf", "step-up",
                   "step up", "glute", "hamstring", "quad", "good morning")),
    ("Back",      ("row", "pull-up", "pullup", "pull up", "pulldown", "lat pull",
                   "latissimus", "chin-up", "chin up", "deadlift", "face pull",
                   "shrug", "back extension")),
    ("Chest",     ("bench", "chest", "push-up", "pushup", "push up", "fly",
                   "pec", "floor press", "dip")),
    ("Shoulders", ("overhead press", "shoulder press", "lateral raise",
                   "front raise", "arnold", "upright row", "pike", "delt",
                   "military", "ohp", "rear delt")),
    ("Triceps",   ("tricep", "skull", "kickback", "close grip", "close-grip",
                   "pushdown", "overhead extension")),
    ("Biceps",    ("curl", "bicep")),
    ("Core",      ("plank", "crunch", "sit-up", "situp", "ab ", "abs",
                   "leg raise", "russian twist", "hanging", "core", "hollow")),
]


def muscle_groups(name: str) -> list[str]:
    """Primary muscle group(s) a lift trains, by keyword. Empty if unknown."""
    n = f" {(name or '').lower()} "
    hits = []
    for muscle, kws in _RULES:
        if any(k in n for k in kws) and muscle not in hits:
            hits.append(muscle)
    # Romanian deadlift is legs, not back — a plain deadlift is both.
    if "romanian" in n or "rdl" in n:
        hits = [m for m in hits if m != "Back"] or ["Legs"]
    return hits


def _prescribed_sets() -> dict[str, int]:
    """name(lower) -> prescribed set count, from the active program, so a
    summary-logged exercise (no per-set list) still counts sensibly."""
    out = {}
    for day in get_program().values():
        for ex in day.get("exercises", []):
            try:
                out[ex["name"].lower()] = int(ex.get("sets") or 3)
            except (TypeError, ValueError, KeyError):
                pass
    return out


def _sets_in(exercise: dict, prescribed: dict[str, int]) -> int:
    sets = exercise.get("sets")
    if isinstance(sets, list) and sets:
        return len(sets)
    return prescribed.get((exercise.get("name") or "").lower(), 3)


def weekly_muscle_volume(log: dict | None = None, week_offset: int = 0) -> list[dict]:
    """Direct working sets per muscle for a calendar week (0 = current).
    Returns [{muscle, sets, low, high, status}] for every landmark muscle,
    ordered by how far out of range it is (most actionable first)."""
    log = log or load_log()
    now = today()
    wk_start = now - timedelta(days=now.weekday()) - timedelta(days=7 * week_offset)
    wk_end = wk_start + timedelta(days=7)
    prescribed = _prescribed_sets()

    tally = {m: 0 for m in LANDMARKS}
    for s in log.get("sessions", []):
        try:
            d = datetime.strptime(s.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (wk_start <= d < wk_end):
            continue
        for ex in s.get("exercises", []):
            n_sets = _sets_in(ex, prescribed)
            for m in muscle_groups(ex.get("name", "")):
                if m in tally:
                    tally[m] += n_sets

    out = []
    for muscle, (low, high) in LANDMARKS.items():
        sets = tally[muscle]
        if muscle == "Core" and sets == 0:
            continue                                   # don't nag about optional core
        status = "low" if sets < low else "high" if sets > high else "ok"
        out.append({"muscle": muscle, "sets": sets, "low": low, "high": high,
                    "status": status})

    def _rank(r):
        if r["status"] == "high":  return (0, -(r["sets"] - r["high"]))
        if r["status"] == "low":   return (1, -(r["low"] - r["sets"]))
        return (2, 0)
    out.sort(key=_rank)
    return out


def format_muscle_volume_block(log: dict | None = None) -> str:
    """Prompt block so the coach can reason about weekly balance."""
    rows = weekly_muscle_volume(log)
    flagged = [r for r in rows if r["status"] != "ok"]
    if not flagged:
        return ""
    lines = ["WEEKLY MUSCLE VOLUME (direct sets this week vs target range):"]
    for r in rows:
        tag = {"low": " — LOW, add volume", "high": " — HIGH, watch recovery", "ok": ""}[r["status"]]
        lines.append(f"- {r['muscle']}: {r['sets']} sets (target {r['low']}-{r['high']}){tag}")
    return "\n".join(lines)
