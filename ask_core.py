"""
Unified natural-language recall across all of the user's data — powered by a
tool-using reasoning loop. The model decides which slices of data to fetch,
inspects them, and reasons step by step before answering.
"""

import logging
from collections import defaultdict

from llm import chat, reason_loop
from agent_core import (
    PROGRAM,
    get_weight_trend,
    load_log,
    load_memory,
    load_profile,
    today_iso,
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
    lines = [f"Sessions{' in ' + month if month else ' (all time)'}: {len(sessions)}"]
    lines.append("Per-month counts: " + ", ".join(f"{m}={c}" for m, c in sorted(by_month.items())))
    for s in sessions[-20:]:
        exs = "; ".join(
            f"{e.get('name')} {e.get('weight','?')}kg x {e.get('reps_done','?')}"
            for e in s.get("exercises", [])[:6]
        )
        day = s.get("day", "?")
        lines.append(f"  {s.get('date','?')} Day {day} ({PROGRAM.get(day,{}).get('name','')}): {exs}")
    return "\n".join(lines)


def query_exercise(name: str) -> str:
    name_l = (name or "").lower()
    rows = []
    best = None
    for s in load_log().get("sessions", []):
        for e in s.get("exercises", []):
            if name_l in (e.get("name", "").lower()):
                w = e.get("weight", "?"); r = e.get("reps_done", "?")
                rows.append(f"  {s.get('date','?')}: {w}kg x {r}")
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
    mem = load_memory()
    entries = mem.get("weight_log", [])[-15:]
    trend = get_weight_trend(mem)
    body = "\n".join(f"  {e}" for e in entries) if entries else "  (no entries)"
    return f"Weight trend: {trend}\nRecent check-ins:\n{body}"


def query_today_workout() -> str:
    """Authoritative: today's training day and its exercises with last weights."""
    from agent_core import get_last_session_for_day, get_next_day, today_iso
    log  = load_log()
    day  = get_next_day(log)
    p    = PROGRAM.get(day, {})
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
]

TOOL_IMPLS = {
    "query_today_workout":     query_today_workout,
    "query_workouts":          query_workouts,
    "query_exercise":          query_exercise,
    "query_spending":          query_spending,
    "query_weight":            query_weight,
    "query_profile":           query_profile,
    "generate_spending_review": generate_spending_review,
    "get_system_status":        get_system_status,
}


def _answer_without_tools(question: str) -> str:
    """Fallback for providers/models that don't support tool calling: dump all
    data into one prompt and answer in a single call."""
    context = "\n\n".join([
        query_profile(),
        query_weight(),
        query_workouts(),
        query_spending(),
    ])
    messages = [
        {"role": "system", "content": (
            f"You are the user's personal data analyst. Today is {today_iso()}. "
            "Answer ONLY from the DATA provided. Never invent numbers; if it's not there, say so. "
            "Be concise and specific with dates, counts and amounts. Use Rs for rupees. Plain text only."
        )},
        {"role": "user", "content": f"DATA:\n{context}\n\nQUESTION: {question}"},
    ]
    return chat(messages, temperature=0.2) or "I couldn't find an answer in your data."


def answer_question(question: str) -> str:
    if not question.strip():
        return ("Ask me anything about your training, weight, meals, or spending.\n"
                "e.g. !ask how many workouts in June, or !ask my best bench press")

    system = (
        f"You are the user's personal data analyst. Today is {today_iso()}.\n"
        "Think step by step. Use the provided tools to fetch ONLY the data you need to "
        "answer the question, inspect the results, and call more tools if needed. "
        "Answer strictly from tool results — never invent numbers; if the data doesn't "
        "contain the answer, say so. Be concise and specific: cite real dates, counts, "
        "and amounts. Use Rs for rupees. Plain text only, no markdown."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    try:
        return reason_loop(messages, TOOLS, TOOL_IMPLS, max_steps=5) or "I couldn't find an answer."
    except Exception as e:
        # Model/provider may not support tool calling — fall back to single-shot.
        log.warning(f"Tool-calling reasoning failed ({e}); using no-tools fallback.")
        try:
            return _answer_without_tools(question)
        except Exception as e2:
            log.error(f"ask fallback error: {e2}", exc_info=True)
            return "Couldn't process that question. Try rephrasing."
