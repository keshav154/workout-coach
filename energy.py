"""
Adaptive TDEE — learn the user's REAL maintenance calories from data instead of
trusting the Mifflin-St Jeor + activity-multiplier guess.

Method (energy balance): over a window where we know both what the user ate and
how their weight moved,

    maintenance = mean_daily_intake  -  (weight_change_kg * 7700 / days)

i.e. if you ate 2100 kcal/day and still lost 0.3 kg/week, your true maintenance
is ABOVE 2100. 7700 kcal ~= the energy in 1 kg of body mass.

The estimate is smoothed against the previous value and clamped to a sane band
around BMR, so a noisy week can't whipsaw the target. It only acts when there's
enough signal (logged intake + a real weigh-in span); otherwise the formula
stays in charge. The learned value is stored in the same `learned_params`
collection as the other adaptive knobs, with a reason and one-tap undo.
"""

import logging
from datetime import datetime, timedelta

from agent_core import (_col, load_memory, load_profile, save_profile,
                        get_weight_entries, today, today_iso)

log = logging.getLogger(__name__)

KCAL_PER_KG = 7700.0          # energy in ~1 kg of body mass
WINDOW_DAYS = 28              # how far back to look
MIN_LOGGED_DAYS = 10         # need this many days with food logged
MIN_COVERAGE = 0.4           # ...and at least this fraction of the span
STALE_AFTER_DAYS = 21        # a learned maintenance older than this is ignored
STORE_KEY = "maintenance_kcal"


def _daily_intake(start_iso: str, end_iso: str) -> dict[str, float]:
    """Total logged kcal per date in [start, end] (only days with food)."""
    out: dict[str, float] = {}
    for m in _col("meals").find():
        d = m.get("date")
        if not d or d < start_iso or d > end_iso:
            continue
        out[d] = out.get(d, 0.0) + float(m.get("calories") or 0)
    return {d: v for d, v in out.items() if v > 0}


def estimate_maintenance() -> dict:
    """Data-derived maintenance estimate, or {'error': why}. Pure read — never
    writes. Returns the estimate plus the inputs, for transparency."""
    profile = load_profile() or {}

    entries = []
    for d, w in get_weight_entries(load_memory()):
        try:
            entries.append((datetime.strptime(d, "%Y-%m-%d").date(), float(w)))
        except (ValueError, TypeError):
            pass
    entries.sort()
    cutoff = today() - timedelta(days=WINDOW_DAYS)
    entries = [e for e in entries if e[0] >= cutoff]
    if len(entries) < 3:
        return {"error": "need at least 3 recent weigh-ins"}

    d0, w0 = entries[0]
    d1, w1 = entries[-1]
    span = (d1 - d0).days
    if span < 14:
        return {"error": "need weigh-ins spanning at least 2 weeks"}

    intake = _daily_intake(d0.isoformat(), d1.isoformat())
    logged_days = len(intake)
    if logged_days < MIN_LOGGED_DAYS or logged_days < span * MIN_COVERAGE:
        return {"error": f"only {logged_days} days of food logged in the window — "
                         "log meals more consistently and I can measure this"}

    mean_intake = sum(intake.values()) / logged_days
    rate_kg_day = (w1 - w0) / span
    rate_kcal_day = rate_kg_day * KCAL_PER_KG
    estimate = mean_intake - rate_kcal_day        # losing (neg rate) => higher maintenance

    from health import _bmr
    bmr = _bmr(profile)
    lo, hi = max(1200, bmr * 1.0), min(4500, bmr * 2.2)
    clamped = int(round(max(lo, min(hi, estimate))))

    return {
        "estimate": clamped,
        "raw_estimate": int(round(estimate)),
        "mean_intake": int(round(mean_intake)),
        "rate_kg_week": round(rate_kg_day * 7, 2),
        "logged_days": logged_days,
        "span_days": span,
        "clamped": clamped != int(round(estimate)),
    }


# ── Learned-value store (shares the learned_params collection) ─────────────────
def get_learned_maintenance() -> int | None:
    """The stored adaptive maintenance, or None if unset or stale (so the
    formula transparently resumes if the user stops logging)."""
    try:
        doc = _col("learned_params").find_one({"_id": STORE_KEY})
    except Exception:
        return None
    if not doc or "value" not in doc:
        return None
    updated = doc.get("updated", "")
    try:
        if (today() - datetime.strptime(updated, "%Y-%m-%d").date()).days > STALE_AFTER_DAYS:
            return None
    except (ValueError, TypeError):
        return None
    try:
        return int(doc["value"])
    except (TypeError, ValueError):
        return None


def maintenance_info() -> dict:
    """For the dashboard / endpoint: current learned maintenance + a fresh
    estimate preview (or the reason there isn't one yet)."""
    stored = None
    try:
        stored = _col("learned_params").find_one({"_id": STORE_KEY})
    except Exception:
        pass
    active = get_learned_maintenance()
    return {
        "active": active,                       # None => formula in charge
        "value": (stored or {}).get("value"),
        "reason": (stored or {}).get("reason", ""),
        "updated": (stored or {}).get("updated", ""),
        "stale": bool(stored) and active is None,
        "preview": estimate_maintenance(),
    }


def update_maintenance() -> str | None:
    """Weekly: recompute maintenance from data, smooth it against the previous
    value, store it (with reason), and hand the baseline over from the trend-
    nudge by zeroing cal_adjust. Returns a notification message, or None."""
    est = estimate_maintenance()
    if "error" in est:
        return None

    prev = get_learned_maintenance()
    if prev is None:
        from agent_core import compute_targets
        # Start from the current formula TDEE so the first move is gentle.
        prev = compute_targets(load_profile() or {}).get("tdee", est["estimate"])

    smoothed = int(round(0.5 * prev + 0.5 * est["estimate"]))
    if abs(smoothed - (get_learned_maintenance() or 0)) < 30 and get_learned_maintenance() is not None:
        return None                             # negligible change — don't churn

    reason = (f"measured from {est['logged_days']}d of food + weight trend "
              f"{est['rate_kg_week']:+.2f}kg/wk (ate ~{est['mean_intake']}, "
              f"est {est['raw_estimate']})")
    rec = {"_id": STORE_KEY, "value": smoothed, "reason": reason[:300],
           "prev": prev, "updated": today_iso()}
    _col("learned_params").update_one({"_id": STORE_KEY}, {"$set": rec}, upsert=True)

    # The recalibrated baseline already reflects real results, so retire any
    # accumulated trend-nudge to avoid double-correcting.
    profile = load_profile() or {}
    if profile.get("cal_adjust"):
        profile["cal_adjust"] = 0
        save_profile(profile)

    try:
        from trust import record_audit
        record_audit("maintenance_kcal", f"{prev} -> {smoothed} kcal ({reason})")
    except Exception:
        pass

    return (f"🔬 Adaptive TDEE: from your logged food and weight trend, your real "
            f"maintenance is about {smoothed} kcal/day (was using {prev}). Your "
            f"targets now come from YOUR measured metabolism, not a formula.")
