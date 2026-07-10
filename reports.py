"""
Scheduled content builders: daily workout nudge and weekly fitness+finance recap.
Called by the /cron endpoints in bot.py.
"""

import logging
from datetime import timedelta

from llm import chat
from agent_core import (
    PROGRAM,
    days_since_last_session,
    get_consecutive_workout_days,
    get_next_day,
    get_weight_trend,
    load_log,
    load_memory,
    load_profile,
    profile_complete,
    today,
    today_iso,
)
from expense_core import monthly_summary
from goals import get_goals, _project_lift, _project_weight
from nutrition import today_totals

log = logging.getLogger(__name__)


def _worked_out_today(log_doc: dict) -> bool:
    now = today_iso()
    return any(s.get("date") == now for s in log_doc.get("sessions", []))


def build_daily_nudge() -> str | None:
    """Morning reminder. Returns None when there's nothing worth sending."""
    profile = load_profile()
    if not profile_complete(profile):
        return "Good morning! Open CoachxKeshav to finish your quick setup and get your first workout plan."

    workout_log = load_log()
    if _worked_out_today(workout_log):
        return None  # already trained today, don't nag

    day  = get_next_day(workout_log)
    p    = PROGRAM.get(day, {})
    name = p.get("name", "")
    gap  = days_since_last_session(workout_log)
    streak = get_consecutive_workout_days(workout_log)

    lines = [f"Good morning {profile.get('name','')}!".strip() + " 💪",
             f"Today is Day {day} - {name}."]

    if gap is not None and gap >= 7:
        lines.append(f"It's been {gap} days since your last session — let's ease back in with lighter weights today.")
    elif streak >= 2:
        lines.append(f"You're on a {streak}-day streak. Keep it going!")
    else:
        lines.append("A quick session today keeps you on track. Let's go!")

    # Autonomous goal-risk check-in: surface any goal that's projected behind,
    # instead of waiting for the user to ask !goals.
    at_risk = _at_risk_goal_lines()
    if at_risk:
        lines.append("")
        lines.append("⚠️ Goal check: " + " ".join(at_risk))

    lines.append("Reply !workout when you're ready.")
    return "\n".join(lines)


def _at_risk_goal_lines() -> list[str]:
    out = []
    for g in get_goals():
        try:
            line = _project_weight(g) if g["kind"] == "weight" else _project_lift(g)
        except Exception:
            continue
        if "behind" in line.lower():
            label = f"Weight {g['target']:g}kg" if g["kind"] == "weight" else f"{g.get('exercise','?')} {g['target']:g}kg"
            out.append(f"{label} is falling behind pace — {line}")
    return out


def build_evening_checkin() -> str | None:
    """Autonomous evening loop: independent of workout activity, proactively
    ask about today's meals if none logged, and this week's weight if none
    logged yet. Returns None if both are already covered."""
    profile = load_profile()
    if not profile_complete(profile):
        return None

    mem = load_memory()
    parts = []

    totals = today_totals()
    if totals["count"] == 0:
        parts.append("Haven't heard about your meals today — what did you eat? "
                     "(You can type it or send a photo.)")
    else:
        parts.append(f"Nice, you've logged {totals['calories']} kcal / {totals['protein_g']}g "
                     f"protein today. Keep it up!")

    now = today()
    week_start = now - timedelta(days=now.weekday())
    last_weight_date = None
    for e in mem.get("weight_log", []):
        try:
            last_weight_date = e.split(": ")[0]
        except Exception:
            pass
    logged_this_week = bool(last_weight_date and last_weight_date >= week_start.isoformat())
    if not logged_this_week:
        parts.append("Also — no weight check-in yet this week. What's your current weight?")

    if len(parts) == 1 and totals["count"] > 0:
        return None  # nutrition logged, weight covered — nothing worth nagging about
    return "🌙 Evening check-in\n\n" + "\n\n".join(parts)


def build_weekly_report() -> str:
    """AI-written Sunday recap combining fitness and finance."""
    profile     = load_profile()
    name        = profile.get("name", "") if profile else ""
    workout_log = load_log()
    mem         = load_memory()
    sessions    = workout_log.get("sessions", [])

    now        = today()
    week_start = now - timedelta(days=now.weekday())
    week_sessions = [s for s in sessions if s.get("date", "") >= week_start.isoformat()]
    week_days  = sorted(set(s.get("date", "") for s in week_sessions))

    days_per_week = profile.get("days_per_week", 4) if profile else 4
    weight_trend  = get_weight_trend(mem)
    spending      = monthly_summary()
    prs_this_week = [pr for pr in mem.get("personal_records", [])][-5:]

    workout_block = (
        f"Workouts this week: {len(week_sessions)} of {days_per_week} target\n"
        f"Workout dates: {', '.join(week_days) if week_days else 'none'}\n"
        f"Body weight trend: {weight_trend}\n"
        f"Recent PRs: {'; '.join(prs_this_week) if prs_this_week else 'none logged'}"
    )

    prompt = f"""You are a friendly personal coach writing {name}'s Sunday weekly recap.
Combine their fitness and spending into one short, motivating message.

FITNESS THIS WEEK:
{workout_block}

SPENDING THIS MONTH:
{spending}

Write a warm recap (under 200 words) that:
1. Celebrates what went well this week (call out any PRs by name)
2. Notes if they hit or missed their workout target ({days_per_week}/week)
3. Gives one honest observation about spending
4. Ends with one specific goal for next week covering fitness AND money

Plain text only, no markdown. Use Rs not the rupee symbol. Be encouraging but honest."""

    try:
        text = chat([{"role": "user", "content": prompt}], temperature=0.7)
        return text or _fallback_weekly(name, len(week_sessions), days_per_week, weight_trend, spending)
    except Exception as e:
        log.error(f"Weekly report error: {e}")
        return _fallback_weekly(name, len(week_sessions), days_per_week, weight_trend, spending)


def _fallback_weekly(name, done, target, weight_trend, spending) -> str:
    return (
        f"Weekly recap for {name}\n\n"
        f"Workouts: {done} of {target} done this week\n"
        f"Weight: {weight_trend}\n\n"
        f"{spending}\n\n"
        f"Next week: aim for all {target} sessions and keep spending in check!"
    )
