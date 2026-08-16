"""
Wearable / health metrics (steps, active calories, resting heart rate).

A PWA can't read Android Health Connect directly, so these arrive one of two
ways: pushed to the token-authenticated /ingest webhook by a phone automation
(e.g. Tasker + a Health Connect plugin reading Samsung Health), or logged
by the user through the coach. Stored per day, keyed by date.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from agent_core import _APP_TZ, _col, today, today_iso

log = logging.getLogger(__name__)


def _local_date(iso: str) -> str | None:
    """Local (app-timezone) date string from a UTC/ISO timestamp."""
    try:
        s = str(iso).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_APP_TZ).date().isoformat()
    except (ValueError, TypeError):
        return None

FIELDS = ("steps", "active_kcal", "resting_hr", "sleep_hours",
          "sleep_score", "energy_score", "hrv")
_RANGES = {
    "steps": (0, 100000), "active_kcal": (0, 10000), "resting_hr": (25, 220),
    "sleep_hours": (0, 24), "sleep_score": (0, 100), "energy_score": (0, 100),
    "hrv": (5, 300),
}
_INT_FIELDS = {"steps", "active_kcal", "resting_hr", "sleep_score", "energy_score", "hrv"}

# Common field-name aliases from Samsung Health / Health Connect / automation
# tools (Tasker, HealthSync, ...), so a push "just works" without exact keys.
_ALIASES = {
    "steps": "steps", "step_count": "steps", "stepcount": "steps", "totalsteps": "steps",
    "active_kcal": "active_kcal", "active_calories": "active_kcal", "activecalories": "active_kcal",
    "active_energy": "active_kcal", "activeenergyburned": "active_kcal", "calories": "active_kcal",
    "kcal": "active_kcal", "energy_burned": "active_kcal",
    "resting_hr": "resting_hr", "resting_heart_rate": "resting_hr", "restingheartrate": "resting_hr",
    "rhr": "resting_hr", "heart_rate": "resting_hr", "hr": "resting_hr",
    "sleep_hours": "sleep_hours", "sleep": "sleep_hours", "sleep_duration": "sleep_hours",
    "sleepduration": "sleep_hours", "sleep_hrs": "sleep_hours", "totalsleep": "sleep_hours",
    "sleep_score": "sleep_score", "sleepscore": "sleep_score",
    "energy_score": "energy_score", "energyscore": "energy_score", "energy": "energy_score",
    "readiness": "energy_score", "readiness_score": "energy_score", "recovery": "energy_score",
    "hrv": "hrv", "heart_rate_variability": "hrv", "heartratevariability": "hrv",
}


def normalize_metrics(raw: dict) -> tuple[dict, list[str]]:
    """Map alias keys to canonical fields. Returns (mapped, unrecognized_keys)."""
    mapped, unknown = {}, []
    for k, v in (raw or {}).items():
        if k == "date":
            continue
        canon = _ALIASES.get(str(k).strip().lower().replace(" ", "_"))
        if canon:
            mapped.setdefault(canon, v)
        else:
            unknown.append(k)
    return mapped, unknown


def record_health(metrics: dict, date_str: str | None = None) -> dict:
    """Upsert the day's metrics. Accepts canonical or aliased keys; only
    known, in-range values are stored."""
    d = date_str or today_iso()
    mapped, _ = normalize_metrics(metrics)
    clean = {}
    for k in FIELDS:
        if mapped.get(k) is None:
            continue
        try:
            v = float(mapped[k])
        except (TypeError, ValueError):
            continue
        # A "sleep" value over 24 is almost certainly minutes — convert to hours.
        if k == "sleep_hours" and v > 24:
            v = v / 60.0
        lo, hi = _RANGES[k]
        if lo <= v <= hi:
            clean[k] = int(round(v)) if k in _INT_FIELDS else round(v, 1)
    if clean:
        _col("health").update_one({"_id": d}, {"$set": clean}, upsert=True)
    return clean


def _extract_value(item: dict, keys: tuple) -> float | None:
    for k in keys:
        if item.get(k) is not None:
            try:
                return float(item[k])
            except (TypeError, ValueError):
                return None
    return None


def parse_wearable_payload(raw: dict) -> dict:
    """Store metrics from either the flat format ({"steps": 9652, ...}) or the
    HealthSync / Samsung-Health export format, where each metric is an ARRAY of
    per-period buckets ({"steps": [{"count", "start_time", "end_time"}], ...},
    {"sleep": [{"duration_seconds", "session_end_time"}], ...}). Records every
    day it finds and returns a summary."""
    if not isinstance(raw, dict):
        return {"days": 0, "today": health_today()}

    array_keys = ("steps", "sleep", "heart_rate", "heartrate", "active_calories",
                  "active_energy", "active_calories_burned", "activecaloriesburned",
                  "calories", "distance")
    if not any(isinstance(raw.get(k), list) for k in array_keys):
        saved = record_health(raw, date_str=(raw.get("date") or None))
        return {"days": 1 if saved else 0, "saved": saved, "today": health_today()}

    per_day: dict[str, dict] = defaultdict(dict)

    # Steps: bucket belongs to the local day of its start_time; keep the max.
    for it in raw.get("steps") or []:
        if not isinstance(it, dict):
            continue
        cnt = _extract_value(it, ("count", "value", "steps"))
        d = _local_date(it.get("start_time") or it.get("end_time") or it.get("time"))
        if cnt is not None and d:
            per_day[d]["steps"] = max(per_day[d].get("steps", 0), cnt)

    # Sleep: sum session durations per wake day (session_end_time).
    sleep_sec: dict[str, float] = defaultdict(float)
    for it in raw.get("sleep") or []:
        if not isinstance(it, dict):
            continue
        dur = _extract_value(it, ("duration_seconds", "duration", "total_seconds"))
        d = _local_date(it.get("session_end_time") or it.get("end_time") or it.get("start_time"))
        if dur and d:
            sleep_sec[d] += dur
    for d, secs in sleep_sec.items():
        per_day[d]["sleep_hours"] = round(secs / 3600, 1)

    # Heart rate samples: resting HR ≈ the day's minimum.
    hr_min: dict[str, float] = {}
    for it in raw.get("heart_rate") or raw.get("heartrate") or []:
        if not isinstance(it, dict):
            continue
        v = _extract_value(it, ("bpm", "value", "min", "count"))
        d = _local_date(it.get("start_time") or it.get("time") or it.get("end_time"))
        if v and d:
            hr_min[d] = min(hr_min.get(d, 1e9), v)
    for d, v in hr_min.items():
        if v < 900:
            per_day[d]["resting_hr"] = v

    # Active calories: sum per day. (Only ACTIVE energy — never total, which
    # includes BMR and would double-count against the eaten target.)
    cal_sum: dict[str, float] = defaultdict(float)
    seen_cal = False
    for key in ("active_calories", "active_energy", "active_calories_burned",
                "activeCaloriesBurned", "calories"):
        arr = raw.get(key)
        if not isinstance(arr, list):
            continue
        seen_cal = True
        for it in arr:
            if not isinstance(it, dict):
                continue
            v = _extract_value(it, ("value", "kcal", "count", "calories", "energy"))
            d = _local_date(it.get("start_time") or it.get("end_time"))
            if v is not None and d:
                cal_sum[d] += v
        if seen_cal:
            break               # first matching calorie array wins (avoid summing dupes)
    for d, c in cal_sum.items():
        per_day[d]["active_kcal"] = round(c)

    stored = []
    for d, metrics in per_day.items():
        if record_health(metrics, date_str=d):
            stored.append(d)
    return {"days": len(stored), "dates": sorted(stored)[-10:], "today": health_today()}


def active_kcal_today(profile: dict | None = None, date_str: str | None = None) -> tuple[int, str]:
    """Active calories burned today. Uses the watch's value if it sent one;
    otherwise estimates from steps (many exports send steps but not calories).
    Returns (kcal, source) where source is 'watch', 'steps', or 'none'."""
    h = health_today(date_str)
    if h.get("active_kcal"):
        return int(h["active_kcal"]), "watch"
    steps = h.get("steps")
    if steps:
        from agent_core import load_profile
        try:
            w = float((profile if profile is not None else (load_profile() or {})).get("weight_kg") or 70)
        except (TypeError, ValueError):
            w = 70
        # ~0.04 kcal per step at 70 kg, scaled by bodyweight.
        return round(steps * 0.04 * (w / 70.0)), "steps"
    return 0, "none"


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


def resting_hr_trend() -> dict:
    """Recent resting-HR vs its ~2-week baseline — a rising RHR flags
    under-recovery. Returns {latest, baseline, elevated}."""
    rows = [r for r in health_week(14) if r.get("resting_hr")]
    if len(rows) < 4:
        return {}
    latest = rows[-1]["resting_hr"]
    baseline_vals = [r["resting_hr"] for r in rows[:-2]]
    baseline = round(sum(baseline_vals) / len(baseline_vals))
    return {"latest": latest, "baseline": baseline,
            "elevated": latest >= baseline + 5}


def format_wearable_block(date_str: str | None = None) -> str:
    """Today's watch + cardio activity for the coach's context, including the
    extra calories burned so the coach can reason about net calories."""
    h = health_today(date_str)
    try:
        from cardio import cardio_today_calories
        cc = cardio_today_calories(date_str)
    except Exception:
        cc = 0
    active, src = active_kcal_today(date_str=date_str)
    bits = []
    if h.get("steps") is not None:       bits.append(f"{h['steps']:,} steps")
    if active:
        bits.append(f"{active} active kcal burned"
                    + (" (estimated from steps)" if src == "steps" else ""))
    if cc:                               bits.append(f"{cc} kcal from logged cardio")
    if h.get("sleep_hours") is not None: bits.append(f"{h['sleep_hours']:g}h sleep")
    if h.get("energy_score") is not None: bits.append(f"watch energy score {h['energy_score']}/100")
    if h.get("resting_hr") is not None:  bits.append(f"resting HR {h['resting_hr']}")
    if h.get("hrv") is not None:         bits.append(f"HRV {h['hrv']}")
    if not bits:
        return ""
    line = "TODAY'S ACTIVITY (from the user's Samsung watch / logged cardio): " + ", ".join(bits) + "."
    burn = int(active) + int(cc or 0)
    if burn:
        line += (f" They burned ~{burn} extra kcal today beyond baseline, so their effective "
                 f"calorie budget is higher — account for this when giving net-calorie or meal advice.")
    tr = resting_hr_trend()
    if tr.get("elevated"):
        line += (f" NOTE: resting HR ({tr['latest']}) is elevated vs baseline ({tr['baseline']}) — "
                 f"a possible under-recovery signal; consider suggesting a lighter day or rest.")
    return line
