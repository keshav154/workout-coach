"""
Smart proactive alerts — pattern-triggered, not clock-triggered.
Each alert kind fires at most once per day (deduped in alerts_state).
"""

import logging
from datetime import datetime, timedelta

from agent_core import (
    _col,
    days_since_last_session,
    get_consecutive_workout_days,
    load_log,
    load_memory,
    load_profile,
    profile_complete,
    today,
    today_iso,
)

log = logging.getLogger(__name__)


def _already_sent(kind: str) -> bool:
    d = _col("alerts_state").find_one({"_id": kind})
    return bool(d and d.get("date") == today_iso())


def _mark(kind: str) -> None:
    _col("alerts_state").update_one(
        {"_id": kind},
        {"$set": {"date": today_iso()}},
        upsert=True,
    )


def _week_spend(weeks_ago: int) -> float:
    now = today()
    start = now - timedelta(days=now.weekday()) - timedelta(weeks=weeks_ago)
    end   = start + timedelta(days=6)
    total = 0.0
    for d in _col("expenses").find({"date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}):
        total += d.get("amount", 0) or 0
    return total


def _last_weight_date(mem: dict):
    last = None
    for e in mem.get("weight_log", []):
        try:
            last = e.split(": ")[0]
        except Exception:
            pass
    return last


def run_checks() -> list[str]:
    """Evaluate all alert rules, return messages to send (and mark them sent)."""
    if not profile_complete(load_profile()):
        return []

    log_doc = load_log()
    mem     = load_memory()
    fired   = []

    # 1) Skipped workouts
    gap = days_since_last_session(log_doc)
    if gap is not None and gap >= 3 and not _already_sent("skipped"):
        fired.append(("skipped",
            f"You haven't logged a workout in {gap} days. Everything okay? "
            f"Even a short session today keeps the momentum going."))

    # 2) Stalled weight check-in
    lw = _last_weight_date(mem)
    if lw:
        try:
            days = (today() - datetime.strptime(lw, "%Y-%m-%d").date()).days
            if days >= 14 and not _already_sent("weight"):
                fired.append(("weight",
                    f"No weight check-in for {days} days. Send your weight so your "
                    f"calorie targets stay accurate."))
        except Exception:
            pass

    # 3) Overspending vs trailing 3-week average
    this_week = _week_spend(0)
    prev_avg  = (_week_spend(1) + _week_spend(2) + _week_spend(3)) / 3
    if prev_avg > 0 and this_week > prev_avg * 1.4 and not _already_sent("spend"):
        pct = int((this_week / prev_avg - 1) * 100)
        fired.append(("spend",
            f"Heads up: you've spent Rs {this_week:,.0f} this week, about {pct}% above "
            f"your recent average of Rs {prev_avg:,.0f}. Type !review for a breakdown."))

    # 4) Streak encouragement (positive nudge)
    streak = get_consecutive_workout_days(log_doc)
    days_per_week = (load_profile() or {}).get("days_per_week", 4)
    if streak >= max(days_per_week - 1, 3) and not _already_sent("streak"):
        fired.append(("streak",
            f"You're on a {streak}-day streak — one more session hits your weekly target. "
            f"Strong week!"))

    out = []
    for kind, msg in fired:
        _mark(kind)
        out.append(msg)
    return out
