"""
Cardio session logging (treadmill, run, walk, cycle, ...). Strength training
is logged via workout_log; cardio is separate so it doesn't disturb the
program rotation but still counts toward activity and net calories.
"""

import logging
from datetime import timedelta

from bson import ObjectId

from agent_core import _col, today, today_iso

log = logging.getLogger(__name__)

CARDIO_TYPES = ["Treadmill walk", "Treadmill run", "Incline walk", "Run",
                "Walk", "Cycle", "Other"]


def log_cardio(ctype: str, minutes: float, distance_km: float = 0,
               incline: float = 0, calories: float = 0,
               date_str: str | None = None) -> dict:
    entry = {
        "date":        date_str or today_iso(),
        "type":        (ctype or "Other")[:40],
        "minutes":     int(round(float(minutes or 0))),
        "distance_km": round(float(distance_km or 0), 2),
        "incline":     round(float(incline or 0), 1),
        "calories":    int(round(float(calories or 0))),
    }
    result = _col("cardio").insert_one(dict(entry))
    entry["id"] = str(result.inserted_id)
    return entry


def delete_cardio(cid: str) -> None:
    try:
        _col("cardio").delete_one({"_id": ObjectId(cid)})
    except Exception:
        _col("cardio").delete_one({"_id": cid})


def get_cardio(date_str: str | None = None) -> list[dict]:
    docs = list(_col("cardio").find({"date": date_str or today_iso()}))
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs


def recent_cardio(n: int = 15) -> list[dict]:
    docs = sorted(_col("cardio").find(), key=lambda d: d.get("date", ""), reverse=True)[:n]
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs


def cardio_today_calories(date_str: str | None = None) -> int:
    return sum(int(c.get("calories") or 0) for c in get_cardio(date_str))


def cardio_week() -> dict:
    """Totals for the last 7 days."""
    start = (today() - timedelta(days=6)).isoformat()
    mins, cals, count = 0, 0, 0
    for c in _col("cardio").find():
        if (c.get("date", "") or "") >= start:
            mins += int(c.get("minutes") or 0)
            cals += int(c.get("calories") or 0)
            count += 1
    return {"minutes": mins, "calories": cals, "sessions": count}
