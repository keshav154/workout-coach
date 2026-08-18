"""
Daily cross-domain briefing — the coach's anticipatory 'what matters today'.

Everywhere else the app reacts to the user; this reads ALL the silos at once
(training, recovery, nutrition, body weight, spending) and has the LLM surface
the one or two insights that actually connect — the causal reads a human coach
would notice but the user won't, because the data lives in separate tabs.

Cached per day in the `briefings` collection so the dashboard stays cheap: it's
generated once (lazily on first view or by the morning cron) and reused, with an
explicit refresh available.
"""

import logging

from agent_core import _col, today_iso

log = logging.getLogger(__name__)


def gather_signals() -> str:
    """A compact, number-rich snapshot across every domain, for the prompt."""
    from agent_core import (compute_targets, get_consecutive_workout_days,
                            get_weight_trend, load_log, load_memory, load_profile)
    from progression import detect_plateaus, weekly_volume
    from checkin import recovery_summary
    from nutrition import week_series
    from expense_core import monthly_summary

    profile = load_profile() or {}
    wlog = load_log()
    mem = load_memory()
    lines = []

    # Training
    vol = weekly_volume(wlog)
    plateaus = detect_plateaus(wlog)
    consec = get_consecutive_workout_days(wlog)
    sessions = wlog.get("sessions", [])
    recent = sessions[-7:]
    lines.append(f"Training: {len(sessions)} sessions all-time, {consec} day streak. "
                 f"Volume this week {vol['this_week']:,}kg vs last {vol['last_week']:,}kg. "
                 f"Recent days trained: {len(recent)} of last 7.")
    lines.append("Plateaus: " + ("; ".join(plateaus) if plateaus else "none"))

    # Recovery
    rec = recovery_summary(log=wlog)
    try:
        from health import resting_hr_trend, week_avg_steps
        rhr = resting_hr_trend()
        steps = week_avg_steps()
    except Exception:
        rhr, steps = None, 0
    rhr_txt = ""
    if rhr:
        rhr_txt = f" Resting HR {rhr['latest']} (baseline {rhr['baseline']}" + \
                  (", ELEVATED" if rhr.get("elevated") else "") + ")."
    lines.append(f"Recovery: readiness {rec['score']}/10 ({rec['label']}, {rec['source']}). "
                 f"{'Avg steps/day ' + format(steps, ',') + '.' if steps else ''}{rhr_txt}")

    # Nutrition (last 7 days adherence)
    targets = compute_targets(profile)
    prot_t = targets.get("protein_target_g", 0)
    cal_t = targets.get("calorie_target", 0)
    series = week_series(7)
    logged = [d for d in series if d["calories"] > 0]
    if logged:
        prot_hit = sum(1 for d in logged if prot_t and d["protein_g"] >= 0.9 * prot_t)
        avg_cal = round(sum(d["calories"] for d in logged) / len(logged))
        avg_prot = round(sum(d["protein_g"] for d in logged) / len(logged))
        lines.append(f"Nutrition: logged {len(logged)}/7 days. Avg {avg_cal} kcal "
                     f"(target {cal_t}), avg {avg_prot}g protein (target {prot_t}); "
                     f"hit protein {prot_hit}/{len(logged)} logged days.")
    else:
        lines.append("Nutrition: nothing logged in the last 7 days.")

    # Body weight + money
    lines.append("Body weight: " + get_weight_trend(mem))
    lines.append("Spending: " + monthly_summary())
    return "\n".join(lines)


def generate_briefing(force: bool = False) -> dict:
    """Today's cross-domain briefing, cached per day. Returns
    {date, text, cached}. Set force=True to regenerate."""
    date = today_iso()
    if not force:
        doc = _col("briefings").find_one({"_id": date})
        if doc and doc.get("text"):
            return {"date": date, "text": doc["text"], "cached": True}

    from agent_core import load_profile, profile_complete
    if not profile_complete(load_profile()):
        return {"date": date, "text": "", "cached": False}

    signals = gather_signals()
    prompt = (
        "You are the user's head coach writing a SHORT daily briefing they see on "
        "their home screen. From the cross-domain signals below, surface the ONE "
        "or TWO most important, ACTIONABLE things for today. Where the data "
        "supports it, connect causes ACROSS domains (e.g. a lift stalling while "
        "sleep and protein are low is probably recovery/fuel, not the program). "
        "Reference real numbers. 2-4 short lines, plain text, no markdown, no "
        "bullet symbols. Encouraging but honest. If everything is on track, say "
        "so in one line and give one small nudge.\n\n"
        f"SIGNALS:\n{signals}"
    )
    try:
        from llm import chat
        text = (chat([{"role": "user", "content": prompt}], temperature=0.5) or "").strip()
    except Exception as e:
        log.error(f"generate_briefing error: {e}")
        return {"date": date, "text": "", "cached": False}
    if not text:
        return {"date": date, "text": "", "cached": False}

    _col("briefings").update_one({"_id": date},
                                 {"$set": {"text": text, "generated": date}}, upsert=True)
    return {"date": date, "text": text, "cached": False}
