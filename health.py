"""
Wearable / health metrics (steps, active calories, resting heart rate).

A PWA can't read Android Health Connect directly, so these arrive one of two
ways: pushed to the token-authenticated /ingest webhook by a phone automation
(e.g. Tasker + a Health Connect plugin reading Samsung Health), or logged
by the user through the coach. Stored per day, keyed by date.
"""

import logging
from datetime import timedelta

from agent_core import _col, today, today_iso

log = logging.getLogger(__name__)

FIELDS = ("steps", "active_kcal", "resting_hr")


def record_health(metrics: dict, date_str: str | None = None) -> dict:
    """Upsert the day's metrics. Only known, in-range fields are stored."""
    d = date_str or today_iso()
    clean = {}
    ranges = {"steps": (0, 100000), "active_kcal": (0, 10000), "resting_hr": (25, 220)}
    for k in FIELDS:
        if metrics.get(k) is None:
            continue
        try:
            v = float(metrics[k])
        except (TypeError, ValueError):
            continue
        lo, hi = ranges[k]
        if lo <= v <= hi:
            clean[k] = int(round(v)) if k != "active_kcal" else round(v)
    if clean:
        _col("health").update_one({"_id": d}, {"$set": clean}, upsert=True)
    return clean


def health_today(date_str: str | None = None) -> dict:
    doc = _col("health").find_one({"_id": date_str or today_iso()}) or {}
    return {k: doc.get(k) for k in FIELDS}


def health_week(days: int = 7) -> list[dict]:
    out = []
    for i in range(days - 1, -1, -1):
        d = (today() - timedelta(days=i)).isoformat()
        row = health_today(d)
        row["date"] = d
        out.append(row)
    return out


def week_avg_steps() -> int:
    vals = [r["steps"] for r in health_week() if r.get("steps")]
    return round(sum(vals) / len(vals)) if vals else 0
