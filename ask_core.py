"""
Unified natural-language recall across all of the user's data — powered by a
tool-using reasoning loop. The model decides which slices of data to fetch,
inspects them, and reasons step by step before answering.
"""

import logging
from collections import defaultdict
from datetime import date

from llm import reason_loop
from agent_core import (
    PROGRAM,
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
]

TOOL_IMPLS = {
    "query_workouts": query_workouts,
    "query_exercise": query_exercise,
    "query_spending": query_spending,
    "query_weight":   query_weight,
    "query_profile":  query_profile,
}


def answer_question(question: str) -> str:
    if not question.strip():
        return ("Ask me anything about your training, weight, meals, or spending.\n"
                "e.g. !ask how many workouts in June, or !ask my best bench press")

    system = (
        f"You are the user's personal data analyst. Today is {date.today().isoformat()}.\n"
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
        log.error(f"ask reasoning error: {e}", exc_info=True)
        return "Couldn't process that question. Try rephrasing."
