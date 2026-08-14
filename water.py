"""
Water intake tracking — deliberately minimal so logging is one tap. A day's
intake is a list of amounts (ml) so undo just drops the last entry; the daily
goal is derived from bodyweight (~35 ml/kg) unless the user sets a custom one.
"""

import logging
from datetime import timedelta

from agent_core import _col, load_profile, today, today_iso

log = logging.getLogger(__name__)

GLASS_ML = 250        # one "glass"; the UI also offers a 500 ml "bottle"


def water_goal_ml(profile: dict | None = None) -> int:
    """Custom goal if set, else ~35 ml per kg bodyweight, rounded to a glass
    and clamped to a sane 2.0-4.5 L range."""
    profile = profile if profile is not None else (load_profile() or {})
    custom = profile.get("water_goal_ml")
    if custom:
        try:
            return max(500, int(custom))
        except (TypeError, ValueError):
            pass
    try:
        w = float(profile.get("weight_kg", 80))
    except (TypeError, ValueError):
        w = 80
    ml = int(round(w * 35 / GLASS_ML) * GLASS_ML)
    return max(2000, min(4500, ml))


def add_water(ml: int, date_str: str | None = None) -> None:
    _col("water").update_one({"_id": date_str or today_iso()},
                             {"$push": {"entries": int(ml)}}, upsert=True)


def undo_water(date_str: str | None = None) -> bool:
    """Remove the most recent entry for the day. Returns True if one was removed."""
    d = date_str or today_iso()
    doc = _col("water").find_one({"_id": d})
    entries = (doc or {}).get("entries", [])
    if not entries:
        return False
    entries.pop()
    _col("water").update_one({"_id": d}, {"$set": {"entries": entries}}, upsert=True)
    return True


def set_water_total(ml: int, date_str: str | None = None) -> None:
    """Replace the day's total (e.g. user says 'I drank 2 litres today')."""
    _col("water").update_one({"_id": date_str or today_iso()},
                             {"$set": {"entries": [int(ml)]}}, upsert=True)


def water_today(date_str: str | None = None) -> dict:
    doc = _col("water").find_one({"_id": date_str or today_iso()}) or {}
    entries = doc.get("entries", [])
    return {"ml": sum(entries), "count": len(entries)}


def water_week(days: int = 7) -> list[dict]:
    """Per-day totals for the last `days` days, oldest first."""
    out = []
    for i in range(days - 1, -1, -1):
        d = (today() - timedelta(days=i)).isoformat()
        out.append({"date": d, "ml": water_today(d)["ml"]})
    return out


def week_avg_ml() -> int:
    week = [w["ml"] for w in water_week() if w["ml"] > 0]
    return round(sum(week) / len(week)) if week else 0
