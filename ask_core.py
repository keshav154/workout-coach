"""
Unified natural-language recall across all of the user's data — powered by a
tool-using reasoning loop. The model decides which slices of data to fetch,
inspects them, and reasons step by step before answering.
"""

import logging
from collections import defaultdict

from llm import chat
from agent_core import (
    get_program,
    get_weight_trend,
    load_log,
    load_memory,
    load_profile,
    _col,
)

log = logging.getLogger(__name__)


# ── Tool implementations (read-only) ──────────────────────────────────────────
def query_workouts(month: str | None = None) -> str:
    sessions = load_log().get("sessions", [])
    if month:
        sessions = [s for s in sessions if (s.get("date") or "").startswith(month)]
    if not sessions:
        return f"No workouts found{' for ' + month if month else ''}."
    by_month = defaultdict(int)
    for s in load_log().get("sessions", []):
        d = s.get("date", "")
        if len(d) >= 7:
            by_month[d[:7]] += 1
    prog = get_program()
    lines = [f"Sessions{' in ' + month if month else ' (all time)'}: {len(sessions)}"]
    lines.append("Per-month counts: " + ", ".join(f"{m}={c}" for m, c in sorted(by_month.items())))
    for s in sessions[-20:]:
        exs = "; ".join(
            f"{e.get('name')} {e.get('weight','?')}kg x {e.get('reps_done','?')}"
            for e in s.get("exercises", [])[:6]
        )
        day = s.get("day", "?")
        lines.append(f"  {s.get('date','?')} Day {day} ({prog.get(day,{}).get('name','')}): {exs}")
    return "\n".join(lines)


def query_exercise(name: str) -> str:
    name_l = (name or "").lower()
    rows = []
    best = None
    for s in load_log().get("sessions", []):
        for e in s.get("exercises", []):
            if name_l in (e.get("name", "").lower()):
                w = e.get("weight", "?"); r = e.get("reps_done", "?")
                note = f"  — note: {e['note']}" if e.get("note") else ""
                rows.append(f"  {s.get('date','?')}: {w}kg x {r}{note}")
                try:
                    cur = (float(w), float(str(r).split('-')[0].split()[0]))
                    if best is None or cur > best[0]:
                        best = (cur, f"{w}kg x {r} on {s.get('date')}")
                except (ValueError, AttributeError):
                    pass
    if not rows:
        return f"No history found for an exercise matching '{name}'."
    out = [f"History for '{name}':"] + rows[-15:]
    if best:
        out.append(f"Best: {best[1]}")
    return "\n".join(out)


def query_spending(month: str | None = None) -> str:
    docs = list(_col("expenses").find())
    if month:
        docs = [d for d in docs if (d.get("date") or "").startswith(month)]
    if not docs:
        return f"No spending found{' for ' + month if month else ''}."
    by_cat = defaultdict(float)
    total = 0.0
    for d in docs:
        amt = d.get("amount", 0) or 0
        by_cat[d.get("category", "Other")] += amt
        total += amt
    cats = ", ".join(f"{c} Rs {v:,.0f}" for c, v in sorted(by_cat.items(), key=lambda x: -x[1]))
    return f"Spending{' in ' + month if month else ' (all time)'}: total Rs {total:,.0f} | {cats}"


def query_weight() -> str:
    from agent_core import get_weight_entries
    mem = load_memory()
    entries = get_weight_entries(mem)[-15:]
    trend = get_weight_trend(mem)
    body = "\n".join(f"  {d}: {kg:g} kg" for d, kg in entries) if entries else "  (no entries)"
    return f"Weight trend: {trend}\nRecent check-ins:\n{body}"


def query_today_workout() -> str:
    """Authoritative: today's training day and its exercises with last weights."""
    from agent_core import get_last_session_for_day, get_next_day, today_iso
    log  = load_log()
    day  = get_next_day(log)
    p    = get_program().get(day, {})
    last = get_last_session_for_day(log, day)
    lines = [f"AUTHORITATIVE — Today is {today_iso()}, training Day {day}: {p.get('name','')}"]
    for ex in p.get("exercises", []):
        prev = None
        if last:
            prev = next((e for e in last.get("exercises", []) if e["name"] == ex["name"]), None)
        lasttxt = f" (last: {prev.get('weight','?')}kg x {prev.get('reps_done','?')})" if prev else " (no history yet)"
        scheme = ex.get("scheme") or f"{ex['sets']} sets x {ex['rep_range']}"
        lines.append(f"  {ex['name']}: {scheme}{lasttxt}")
    return "\n".join(lines)


def generate_spending_review(month: str | None = None) -> str:
    """Rich AI-written spending analysis (patterns, overspend areas, tips) —
    use when the user wants a real review/analysis of their spending, not just totals."""
    from expense_core import build_review_prompt
    prompt, err = build_review_prompt(month)
    if err:
        return err
    try:
        return chat([{"role": "user", "content": prompt}], temperature=0.7) or "Could not generate a review."
    except Exception as e:
        log.error(f"spending review tool error: {e}")
        return "Could not generate a review right now."


def get_system_status() -> str:
    """Operational status: uptime, DB health, last scheduled job runs. Use when
    the user asks if things are working / is anything broken / system health."""
    from monitor import get_status
    return get_status()


def query_memory_tool(query: str) -> str:
    from memory_core import query_memory
    return query_memory(query)


def query_measurements() -> str:
    """Body measurement history grouped by part, with the change over time."""
    rows = sorted(_col("measurements").find(), key=lambda d: d.get("date", ""))
    if not rows:
        return "No body measurements logged yet."
    by_part: dict[str, list] = defaultdict(list)
    for d in rows:
        if d.get("part") and d.get("cm"):
            by_part[d["part"]].append((d.get("date", "?"), float(d["cm"])))
    lines = ["Body measurements:"]
    for part, vals in sorted(by_part.items()):
        first_d, first = vals[0]
        last_d, last = vals[-1]
        delta = f" ({last - first:+.1f} cm since {first_d})" if len(vals) > 1 else ""
        recent = ", ".join(f"{d}: {v:g}" for d, v in vals[-4:])
        lines.append(f"  {part}: {last:g} cm on {last_d}{delta} | recent: {recent}")
    return "\n".join(lines)


def query_profile() -> str:
    p   = load_profile() or {}
    mem = load_memory()
    return (
        f"Profile: name={p.get('name')}, age={p.get('age')}, weight={p.get('weight_kg')}kg, "
        f"height={p.get('height_cm')}cm, goal={p.get('goal')}, days/week={p.get('days_per_week')}\n"
        f"Personal records: {mem.get('personal_records', [])[-12:]}\n"
        f"Coach observations: {mem.get('coach_observations', [])[-8:]}\n"
        f"Preferences: {mem.get('preferences', [])[-8:]}\n"
        f"Injuries/soreness: {mem.get('injuries_soreness', [])[-8:]}"
    )


TOOLS = [
    {"type": "function", "function": {
        "name": "query_today_workout",
        "description": "Get the authoritative training day for TODAY and its exercises with the user's last weights. Use this whenever the user asks what today's workout is.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "query_workouts",
        "description": "List workout sessions with exercises, weights and reps. Optionally filter by month.",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string", "description": "Month as YYYY-MM, optional"}}},
    }},
    {"type": "function", "function": {
        "name": "query_exercise",
        "description": "Get the full history and personal best for one exercise by name (e.g. 'bench press').",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "query_spending",
        "description": "Get spending totals broken down by category. Optionally filter by month.",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string", "description": "Month as YYYY-MM, optional"}}},
    }},
    {"type": "function", "function": {
        "name": "query_weight",
        "description": "Get body-weight check-in history and the overall trend.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "query_profile",
        "description": "Get the user's profile, goals, personal records, preferences and injuries.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "query_measurements",
        "description": "Get body measurement history (waist, chest, arm, thigh, ...) with changes over time.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "generate_spending_review",
        "description": "Generate a full AI analysis of spending (patterns, overspend areas, tips) for a month. Use when the user wants a real review/analysis, not just a total.",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string", "description": "Month as YYYY-MM, optional (defaults to current month)"}}},
    }},
    {"type": "function", "function": {
        "name": "get_system_status",
        "description": "Check operational health: uptime, database status, last scheduled job runs. Use when the user asks if things are working, if anything's broken, or about system status.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "query_memory",
        "description": "Search past conversations (daily episode summaries), memory notes, and lessons by keyword. Use when the user references something from a previous day/conversation that isn't in the current context (e.g. 'what did I say last week about my shoulder?').",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]},
    }},
]

TOOL_IMPLS = {
    "query_today_workout":     query_today_workout,
    "query_workouts":          query_workouts,
    "query_exercise":          query_exercise,
    "query_spending":          query_spending,
    "query_weight":            query_weight,
    "query_profile":           query_profile,
    "query_measurements":      query_measurements,
    "generate_spending_review": generate_spending_review,
    "get_system_status":        get_system_status,
    "query_memory":             query_memory_tool,
}
