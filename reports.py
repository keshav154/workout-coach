"""
Scheduled content builders: daily workout nudge and weekly fitness+finance recap.
Called by the /cron endpoints in bot.py.
"""

import logging
from datetime import datetime, timedelta

from llm import chat
from agent_core import (
    get_program,
    days_since_last_session,
    effective_calorie_target,
    get_consecutive_workout_days,
    get_next_day,
    get_weight_trend,
    load_log,
    load_memory,
    load_profile,
    profile_complete,
    save_profile,
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

    from agent_core import is_rest_day
    workout_log = load_log()
    if _worked_out_today(workout_log):
        return None  # already trained today, don't nag
    if is_rest_day():
        return None  # deliberate rest day — don't nag to train

    day  = get_next_day(workout_log)
    p    = get_program().get(day, {})
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

    lines.append("Just message me when you're ready and I'll walk you through it.")
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

    from agent_core import get_weight_entries
    now = today()
    week_start = now - timedelta(days=now.weekday())
    w_entries = get_weight_entries(mem)
    last_weight_date = w_entries[-1][0] if w_entries else None
    logged_this_week = bool(last_weight_date and last_weight_date >= week_start.isoformat())
    if not logged_this_week:
        parts.append("Also — no weight check-in yet this week. What's your current weight?")

    # Water short of goal
    from water import water_goal_ml, water_today
    wt, wgoal = water_today()["ml"], water_goal_ml(profile)
    if wgoal and wt < wgoal * 0.8:
        parts.append(f"💧 You're at {wt/1000:.1f}L of your {wgoal/1000:.1f}L water goal — "
                     f"get another glass or two in before bed.")

    # Tracked habits still pending today
    from agent_core import _col
    habit_names = [d["_id"] for d in _col("habits").find()]
    if habit_names:
        done = {l.get("name") for l in _col("habit_log").find()
                if l.get("date") == today_iso()}
        pending = [h for h in habit_names if h not in done]
        if pending:
            parts.append("Habit check — still pending today: " + ", ".join(pending) + ".")

    if len(parts) == 1 and totals["count"] > 0:
        return None  # nutrition logged, weight covered — nothing worth nagging about
    return "🌙 Evening check-in\n\n" + "\n\n".join(parts)


def auto_adjust_calories() -> str | None:
    """Autonomous weekly calorie tuning: compare the recent weigh-in trend to
    the user's goal and write an adjustment back to the profile (cal_adjust,
    ±200 per week, clamped ±600 total by effective_calorie_target). Returns a
    notification message, or None when there's nothing to change."""
    profile = load_profile()
    if not profile_complete(profile):
        return None

    from agent_core import get_weight_entries
    entries = []
    for d, w in get_weight_entries(load_memory()):
        try:
            entries.append((datetime.strptime(d, "%Y-%m-%d").date(), w))
        except ValueError:
            pass
    recent = [x for x in entries if x[0] >= today() - timedelta(days=35)]
    if len(recent) < 3 or (recent[-1][0] - recent[0][0]).days < 14:
        return None                     # not enough signal to act on

    span = (recent[-1][0] - recent[0][0]).days
    rate = (recent[-1][1] - recent[0][1]) / span * 7    # kg per week

    goal = (profile.get("goal") or "").lower()
    step = 0
    if any(k in goal for k in ("lose", "fat", "cut")):
        if rate > -0.15:   step = -200      # not losing
        elif rate < -0.8:  step = +200      # losing too fast
    elif any(k in goal for k in ("gain", "muscle", "bulk")):
        if rate > 0.45:    step = -200      # gaining too fast
        elif rate < 0.05:  step = +200      # not gaining
    else:                                   # recomposition
        if rate > 0.35:    step = -200
        elif rate < -0.35: step = +200
    if not step:
        return None

    try:
        old = int(profile.get("cal_adjust", 0) or 0)
    except (TypeError, ValueError):
        old = 0
    new = max(-600, min(600, old + step))
    if new == old:
        return None                     # already at the clamp

    profile["cal_adjust"] = new
    save_profile(profile)
    from trust import record_audit
    record_audit("cal_adjust", f"{old:+d} -> {new:+d} kcal (trend {rate:+.2f} kg/week)")

    target = effective_calorie_target(profile)
    direction = "down" if step < 0 else "up"
    return (f"🍽️ Calorie auto-adjust: your weight trend is {rate:+.2f} kg/week for a goal of "
            f"'{profile.get('goal')}', so I've moved your daily target {direction} by "
            f"{abs(step)} kcal to {target} kcal. I'll keep tuning this weekly from your weigh-ins.")


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


def weekly_summary_data(week_offset: int = 0) -> dict:
    """Deterministic (no-LLM) structured recap for a calendar week —
    week_offset=0 is the current week, 1 the previous, etc. Powers the
    printable weekly summary; fast and never depends on the model."""
    from agent_core import _col, get_weight_entries
    from progression import detect_plateaus, session_volume
    from nutrition import get_meals

    profile = load_profile() or {}
    log_doc = load_log()
    now = today()
    week_start = now - timedelta(days=now.weekday()) - timedelta(days=7 * week_offset)
    week_end = week_start + timedelta(days=6)

    def in_week(d: str) -> bool:
        return week_start.isoformat() <= (d or "") <= week_end.isoformat()

    prog = get_program()
    sessions = [s for s in log_doc.get("sessions", []) if in_week(s.get("date", ""))]
    session_rows, volume, minutes = [], 0.0, 0
    for s in sorted(sessions, key=lambda x: x.get("date", "")):
        v = session_volume(s)
        volume += v
        minutes += int(s.get("duration_min") or 0)
        session_rows.append({
            "date": s.get("date"), "day": s.get("day"),
            "name": prog.get(s.get("day"), {}).get("name", ""),
            "exercises": len(s.get("exercises", [])),
            "volume": round(v), "duration": s.get("duration_min"),
        })

    # Weight change within the week
    entries = [e for e in get_weight_entries(load_memory()) if in_week(e[0])]
    weight = None
    if entries:
        weight = {"start": entries[0][1], "end": entries[-1][1],
                  "change": round(entries[-1][1] - entries[0][1], 1)}

    # Nutrition adherence: days a meal was logged, avg kcal/protein on those days
    meal_days: dict[str, list] = {}
    for i in range(7):
        d = (week_start + timedelta(days=i)).isoformat()
        if d > now.isoformat():
            continue
        ms = get_meals(d)
        if ms:
            meal_days[d] = ms
    nutrition = None
    if meal_days:
        days = len(meal_days)
        cal = sum(sum(m.get("calories", 0) for m in ms) for ms in meal_days.values())
        prot = sum(sum(m.get("protein_g", 0) for m in ms) for ms in meal_days.values())
        nutrition = {"days_logged": days, "avg_calories": round(cal / days),
                     "avg_protein": round(prot / days)}

    # Spending in the week
    spend_total, spend_cats = 0.0, {}
    for e in _col("expenses").find():
        if in_week(e.get("date", "")):
            amt = e.get("amount", 0) or 0
            spend_total += amt
            spend_cats[e.get("category", "Other")] = spend_cats.get(e.get("category", "Other"), 0) + amt

    from water import week_avg_ml
    days_per_week = profile.get("days_per_week", 6)
    return {
        "name": profile.get("name", ""),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "water_avg_ml": week_avg_ml(),
        "sessions": session_rows,
        "sessions_done": len(session_rows),
        "sessions_target": days_per_week,
        "total_volume": round(volume),
        "total_minutes": minutes,
        "weight": weight,
        "nutrition": nutrition,
        "plateaus": detect_plateaus(log_doc),
        "prs": load_memory().get("personal_records", [])[-6:],
        "spending": {"total": round(spend_total),
                     "by_category": sorted(spend_cats.items(), key=lambda x: -x[1])},
    }
