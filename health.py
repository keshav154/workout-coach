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
    bits = []
    if h.get("steps") is not None:       bits.append(f"{h['steps']:,} steps")
    if h.get("active_kcal") is not None: bits.append(f"{h['active_kcal']} active kcal burned")
    if cc:                               bits.append(f"{cc} kcal from logged cardio")
    if h.get("sleep_hours") is not None: bits.append(f"{h['sleep_hours']:g}h sleep")
    if h.get("energy_score") is not None: bits.append(f"watch energy score {h['energy_score']}/100")
    if h.get("resting_hr") is not None:  bits.append(f"resting HR {h['resting_hr']}")
    if h.get("hrv") is not None:         bits.append(f"HRV {h['hrv']}")
    if not bits:
        return ""
    line = "TODAY'S ACTIVITY (from the user's Samsung watch / logged cardio): " + ", ".join(bits) + "."
    burn = int(h.get("active_kcal") or 0) + int(cc or 0)
    if burn:
        line += (f" They burned ~{burn} extra kcal today beyond baseline, so their effective "
                 f"calorie budget is higher — account for this when giving net-calorie or meal advice.")
    tr = resting_hr_trend()
    if tr.get("elevated"):
        line += (f" NOTE: resting HR ({tr['latest']}) is elevated vs baseline ({tr['baseline']}) — "
                 f"a possible under-recovery signal; consider suggesting a lighter day or rest.")
    return line
