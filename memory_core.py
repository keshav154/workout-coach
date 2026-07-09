"""
Episodic memory and lessons.

- Episodes: short daily summaries of what happened in conversation, written by
  the LLM each evening — the agent remembers *experience*, not just data rows.
- Lessons: rules distilled from moments the user corrected the agent — injected
  into every prompt so mistakes aren't repeated.
- Consolidation: a weekly autonomous pass that prunes stale memory items and
  distills episodes into durable observations, so memory improves instead of rotting.
"""

import logging

from llm import chat
from agent_core import (
    DEFAULT_MEMORY,
    _col,
    load_history,
    load_memory,
    save_memory,
    today_iso,
)

log = logging.getLogger(__name__)

MAX_LESSONS_IN_PROMPT  = 8
MAX_EPISODES_IN_PROMPT = 3


# ── Lessons (learning from corrections) ───────────────────────────────────────
def save_lesson(mistake: str, rule: str) -> None:
    _col("lessons").insert_one({
        "date": today_iso(),
        "mistake": (mistake or "")[:300],
        "rule": (rule or "")[:300],
    })


def get_lessons(n: int = MAX_LESSONS_IN_PROMPT) -> list[dict]:
    return list(_col("lessons").find().sort("_id", -1).limit(n))


def format_lessons_block() -> str:
    lessons = get_lessons()
    if not lessons:
        return ""
    lines = ["LESSONS FROM PAST MISTAKES (the user corrected you on these — do not repeat them):"]
    for l in reversed(lessons):
        lines.append(f"- {l.get('rule')}")
    return "\n".join(lines)


# ── Episodes (daily conversation summaries) ───────────────────────────────────
def save_episode(date_str: str, summary: str) -> None:
    _col("episodes").update_one(
        {"_id": date_str},
        {"$set": {"summary": (summary or "")[:600]}},
        upsert=True,
    )


def get_recent_episodes(n: int = MAX_EPISODES_IN_PROMPT) -> list[dict]:
    docs = list(_col("episodes").find().sort("_id", -1).limit(n))
    return [{"date": d["_id"], "summary": d.get("summary", "")} for d in docs]


def format_episodes_block() -> str:
    eps = get_recent_episodes()
    if not eps:
        return ""
    lines = ["RECENT DAYS (episodic memory — context from previous conversations):"]
    for e in reversed(eps):
        lines.append(f"- {e['date']}: {e['summary']}")
    return "\n".join(lines)


def summarize_today() -> str | None:
    """Autonomous daily episode: distill today's conversations (all surfaces)
    into a 2-3 line summary. Returns the summary, or None if nothing happened."""
    turns = []
    for source in ("web", "telegram", "whatsapp", "discord"):
        for m in load_history(source)[-14:]:
            role = "User" if m.get("role") == "user" else "Coach"
            content = (m.get("content") or "").strip()
            if content:
                turns.append(f"{role}: {content[:200]}")
    if not turns:
        return None
    prompt = (
        "Summarize this fitness-coach conversation from today into 2-3 short lines "
        "capturing what actually happened and anything notable about the user's state "
        "(trained or skipped, what they ate, mood/energy, injuries, decisions made, "
        "corrections they gave you). Write in third person, past tense, plain text.\n\n"
        + "\n".join(turns[-40:])
    )
    try:
        summary = chat([{"role": "user", "content": prompt}], temperature=0.3).strip()
    except Exception as e:
        log.error(f"Episode summary failed: {e}")
        return None
    if not summary:
        return None
    save_episode(today_iso(), summary)
    return summary


# ── Consolidation (weekly autonomous memory hygiene) ──────────────────────────
def consolidate_memory() -> str | None:
    """Weekly pass: have the LLM prune stale items, merge duplicates, and
    distill episodes into durable coach observations. Returns a short report."""
    mem = load_memory()
    episodes = list(_col("episodes").find().sort("_id", -1).limit(14))
    ep_lines = [f"{d['_id']}: {d.get('summary','')}" for d in reversed(episodes)]

    mem_view = {k: v for k, v in mem.items() if k != "weight_log" and isinstance(v, list) and v}
    if not mem_view and not ep_lines:
        return None

    prompt = (
        "You are doing memory hygiene for a fitness coach agent. Given the agent's "
        "memory lists and recent daily episodes, produce a CLEANED version of the "
        "memory as JSON with the same keys. Rules: merge duplicates; drop stale "
        "soreness/injury notes older than ~2 weeks unless chronic; keep personal "
        "records; distill recurring patterns from the episodes into 1-3 concise "
        "coach_observations; keep each list under 10 items; never invent facts.\n\n"
        f"MEMORY:\n{mem_view}\n\nEPISODES:\n" + "\n".join(ep_lines) +
        "\n\nReturn ONLY the JSON object, no prose."
    )
    try:
        import json as _json
        import re as _re
        raw = chat([{"role": "user", "content": prompt}], temperature=0.2)
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if not m:
            return None
        cleaned = _json.loads(m.group(0))
    except Exception as e:
        log.error(f"Memory consolidation failed: {e}")
        return None

    changed = []
    for key in DEFAULT_MEMORY:
        if key == "weight_log":            # never let the LLM touch weight history
            continue
        if key in cleaned and isinstance(cleaned[key], list):
            new_items = [str(i)[:300] for i in cleaned[key]][:10]
            if new_items != mem.get(key, []):
                changed.append(f"{key}: {len(mem.get(key, []))} -> {len(new_items)}")
            mem[key] = new_items
    if not changed:
        return None
    save_memory(mem)
    return "Memory consolidated: " + "; ".join(changed)


# ── query_memory read tool ────────────────────────────────────────────────────
def query_memory(query: str) -> str:
    """Keyword search across episodes, memory lists, and lessons — lets the
    model recall things beyond what's injected into the prompt."""
    q = (query or "").lower().strip()
    if not q:
        return "Give a search term."
    words = [w for w in q.split() if len(w) > 2]
    hits = []

    for d in _col("episodes").find().sort("_id", -1).limit(60):
        text = d.get("summary", "")
        if any(w in text.lower() for w in words):
            hits.append(f"[{d['_id']}] {text}")

    mem = load_memory()
    for key, items in mem.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if any(w in str(item).lower() for w in words):
                hits.append(f"[{key}] {item}")

    for l in _col("lessons").find().sort("_id", -1).limit(40):
        blob = f"{l.get('mistake','')} {l.get('rule','')}"
        if any(w in blob.lower() for w in words):
            hits.append(f"[lesson {l.get('date','')}] {l.get('rule','')}")

    if not hits:
        return f"No memories matching '{query}'."
    return "Memory search results:\n" + "\n".join(hits[:12])
