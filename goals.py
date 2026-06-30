"""
Goal setting + projection. Supports a body-weight goal (target kg by a date)
and lift goals (target weight on an exercise). Projects ETA from real trend data.
"""

import logging
from datetime import date, datetime

from agent_core import _col, _num, load_log, load_memory

log = logging.getLogger(__name__)


def set_goal(kind: str, target: float, by_date: str | None = None, exercise: str | None = None) -> dict:
    goal = {
        "kind":     kind,                       # "weight" or "lift"
        "target":   float(target),
        "by_date":  by_date,
        "exercise": exercise,
        "created":  date.today().isoformat(),
    }
    _col("goals").insert_one(dict(goal))
    return goal


def get_goals() -> list[dict]:
    out = []
    for d in _col("goals").find():
        d["id"] = str(d.pop("_id"))
        out.append(d)
    return out


def clear_goals() -> int:
    return _col("goals").delete_many({}).deleted_count


def _weight_series() -> list[tuple[date, float]]:
    rows = []
    for e in load_memory().get("weight_log", []):
        try:
            d, w = e.split(": ")
            rows.append((datetime.strptime(d, "%Y-%m-%d").date(), float(w.replace(" kg", ""))))
        except (ValueError, AttributeError):
            pass
    rows.sort()
    return rows


def _best_lift(exercise: str) -> tuple[date, float] | None:
    best = None
    ex_l = (exercise or "").lower()
    for s in load_log().get("sessions", []):
        try:
            d = datetime.strptime(s.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        for e in s.get("exercises", []):
            if ex_l in e.get("name", "").lower():
                w = _num(e.get("weight"))
                if w > 0 and (best is None or w > best[1]):
                    best = (d, w)
    return best


def _project_weight(goal: dict) -> str:
    series = _weight_series()
    if len(series) < 2:
        return "need at least 2 weight check-ins to project."
    (d0, w0), (d1, w1) = series[0], series[-1]
    days = max((d1 - d0).days, 1)
    rate = (w1 - w0) / days            # kg per day
    target = goal["target"]
    remaining = target - w1
    if abs(rate) < 1e-6 or (remaining > 0) != (rate > 0):
        return f"at {w1}kg, target {target}kg — current trend is flat/wrong direction, adjust calories."
    eta_days = remaining / rate
    eta = (d1 + _timedelta(eta_days)).isoformat()
    pace = abs(rate) * 7
    line = f"at {w1}kg, target {target}kg → ~{abs(remaining):.1f}kg to go at {pace:.2f}kg/week, ETA ~{eta}"
    if goal.get("by_date"):
        try:
            target_d = datetime.strptime(goal["by_date"], "%Y-%m-%d").date()
            on_track = (d1 + _timedelta(eta_days)) <= target_d
            line += f" ({'on track' if on_track else 'behind'} for {goal['by_date']})"
        except ValueError:
            pass
    return line


def _timedelta(days: float):
    from datetime import timedelta
    return timedelta(days=round(days))


def _project_lift(goal: dict) -> str:
    ex = goal.get("exercise", "")
    best = _best_lift(ex)
    target = goal["target"]
    if not best:
        return f"{ex}: no logged history yet, target {target:g}kg."
    _, w = best
    if w >= target:
        return f"{ex}: hit {w:g}kg — goal of {target:g}kg reached!"
    return f"{ex}: best {w:g}kg, target {target:g}kg → {target - w:g}kg to go."


def goals_status() -> str:
    goals = get_goals()
    if not goals:
        return "No goals set. Try: !goal weight 90 by 2026-09-01  or  !goal lift bench 24"
    lines = ["Your goals:", ""]
    for g in goals:
        if g["kind"] == "weight":
            lines.append(f"Weight {g['target']:g}kg" + (f" by {g['by_date']}" if g.get("by_date") else "")
                         + f"\n  {_project_weight(g)}")
        else:
            lines.append(f"Lift {g.get('exercise','?')} {g['target']:g}kg"
                         + f"\n  {_project_lift(g)}")
    return "\n".join(lines)


def format_goals_block() -> str:
    goals = get_goals()
    if not goals:
        return ""
    lines = ["ACTIVE GOALS (reference these to motivate and keep the user on pace):"]
    for g in goals:
        if g["kind"] == "weight":
            lines.append(f"- {_project_weight(g)}")
        else:
            lines.append(f"- {_project_lift(g)}")
    return "\n".join(lines)


def parse_goal_command(text: str) -> dict | None:
    """
    !goal weight 90 by 2026-09-01
    !goal lift bench 24
    """
    parts = text.split()
    if len(parts) < 3:
        return None
    kind = parts[1].lower()
    try:
        if kind == "weight":
            target = float(parts[2])
            by = None
            if "by" in parts:
                by = parts[parts.index("by") + 1]
            return {"kind": "weight", "target": target, "by_date": by, "exercise": None}
        if kind == "lift":
            # !goal lift <exercise...> <target>
            target = float(parts[-1])
            exercise = " ".join(parts[2:-1])
            return {"kind": "lift", "target": target, "by_date": None, "exercise": exercise}
    except (ValueError, IndexError):
        return None
    return None
