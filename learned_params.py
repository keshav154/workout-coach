"""
Adaptive parameters the coach learns about THIS user, instead of baking every
constant into code.

Design contract:
- Deterministic modules read values through get_param() and ALWAYS fall back to
  their hardcoded default when nothing has been learned yet. The formula is the
  safe floor; the agent only ever tunes WITHIN the guardrails declared here.
- Only keys in PARAM_SPECS are tunable, and every value is clamped to [min,max]
  and coerced to the declared type — so a bad LLM suggestion can never push the
  app into an unsafe state.
- Every change stores its reason and the previous value, so it's explainable on
  the dashboard and one-tap undoable (reset_param).
- reflect_and_tune() is the weekly agentic loop: it reviews the user's real data
  and proposes adjustments. It is the ONLY writer that comes from the LLM.
"""

import logging

from agent_core import _col, today_iso

log = logging.getLogger(__name__)

# ── The tunable surface ───────────────────────────────────────────────────────
# Each spec: default value, hard [min,max] guardrail, type, and a plain-English
# description the reflection loop sees so it knows what it's allowed to change.
PARAM_SPECS: dict[str, dict] = {
    "plateau_lookback": {
        "default": 3, "min": 2, "max": 6, "type": "int",
        "desc": "How many recent sessions of a lift to look back over before "
                "calling it a plateau. Lower = flags stalls sooner; higher = "
                "more patient. Raise it for a user who progresses slowly.",
    },
    "rep_increment": {
        "default": 1, "min": 1, "max": 3, "type": "int",
        "desc": "How many reps to add per session when holding weight in the "
                "rep-adding phase of double progression. Raise it only if the "
                "user consistently blows past rep targets.",
    },
    "deload_week_factor": {
        "default": 0.6, "min": 0.5, "max": 0.75, "type": "float",
        "desc": "Weight multiplier for a scheduled DELOAD WEEK (0.6 = 40% "
                "lighter). Higher = a gentler deload for someone who recovers "
                "well; lower = a deeper cut.",
    },
    "deload_flag_factor": {
        "default": 0.9, "min": 0.82, "max": 0.95, "type": "float",
        "desc": "Weight multiplier for a single plateaued exercise's auto-"
                "deload (0.9 = 10% lighter). Lower = a bigger back-off for a "
                "stubborn plateau.",
    },
    "recovery_fatigue_streak": {
        "default": 6, "min": 4, "max": 8, "type": "int",
        "desc": "Consecutive training days before recovery readiness starts "
                "getting docked for accumulated fatigue. Lower for a user who "
                "clearly needs more rest; higher for a proven high-frequency "
                "trainer.",
    },
    "autoreg_threshold": {
        "default": 4, "min": 3, "max": 6, "type": "int",
        "desc": "Recovery readiness (1-10) at or below which today's working "
                "weights are automatically eased. Raise it for a user who "
                "clearly performs badly on mediocre-recovery days.",
    },
    "autoreg_max_trim": {
        "default": 0.1, "min": 0.05, "max": 0.2, "type": "float",
        "desc": "Largest fraction to ease the working weight on a very-low-"
                "readiness day (0.1 = up to 10% lighter). Autoregulation only "
                "ever backs off, never adds load.",
    },
}


def _coerce_clamp(key: str, value):
    """Coerce to the spec's type and clamp into [min,max]. Returns None if the
    value can't be interpreted as a number at all."""
    spec = PARAM_SPECS[key]
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    v = max(spec["min"], min(spec["max"], v))
    return int(round(v)) if spec["type"] == "int" else round(v, 3)


# ── Read / write ──────────────────────────────────────────────────────────────
def get_param(key: str):
    """Learned value for `key`, or its hardcoded default. Always clamped, so
    callers can trust the range regardless of what's stored."""
    spec = PARAM_SPECS.get(key)
    if spec is None:
        raise KeyError(f"unknown learned param: {key}")
    try:
        doc = _col("learned_params").find_one({"_id": key})
    except Exception:
        # Store unreachable (no DB in a pure-formula context) — the hardcoded
        # default is always the safe floor, so degrade to it silently.
        return spec["default"]
    if doc and "value" in doc:
        clamped = _coerce_clamp(key, doc["value"])
        if clamped is not None:
            return clamped
    return spec["default"]


def set_param(key: str, value, reason: str = "") -> dict | None:
    """Store a learned value (clamped to guardrails). Returns the stored record,
    or None if the key is unknown or the value isn't numeric. No-ops (and returns
    None) when the clamped value equals what's already effective, so the audit
    log doesn't fill with churn."""
    if key not in PARAM_SPECS:
        log.warning(f"set_param: ignoring unknown key {key!r}")
        return None
    clamped = _coerce_clamp(key, value)
    if clamped is None:
        return None
    prev = get_param(key)
    if clamped == prev:
        return None
    rec = {"_id": key, "value": clamped, "reason": (reason or "")[:300],
           "prev": prev, "updated": today_iso()}
    _col("learned_params").update_one({"_id": key}, {"$set": rec}, upsert=True)
    return rec


def reset_param(key: str) -> bool:
    """Undo a learned value, reverting `key` to its hardcoded default."""
    _col("learned_params").delete_one({"_id": key})
    return True


def all_params() -> list[dict]:
    """Every tunable param with its current effective value, default, and (if
    learned) the reason + when — for the dashboard / transparency."""
    stored = {d["_id"]: d for d in _col("learned_params").find()}
    out = []
    for key, spec in PARAM_SPECS.items():
        d = stored.get(key)
        out.append({
            "key": key,
            "value": get_param(key),
            "default": spec["default"],
            "learned": bool(d),
            "reason": (d or {}).get("reason", ""),
            "updated": (d or {}).get("updated", ""),
            "min": spec["min"], "max": spec["max"], "desc": spec["desc"],
        })
    return out


def format_learned_block() -> str:
    """Short prompt block so the coach can explain its own adapted behaviour."""
    learned = [p for p in all_params() if p["learned"]]
    if not learned:
        return ""
    lines = ["ADAPTED SETTINGS (you learned these for this user — mention them if "
             "asked why a suggestion changed):"]
    for p in learned:
        lines.append(f"- {p['key']} = {p['value']} (default {p['default']}): {p['reason']}")
    return "\n".join(lines)


# ── The weekly agentic reflection loop ────────────────────────────────────────
def _reflection_context() -> str:
    """Assemble the real data the reflection pass reasons over."""
    from agent_core import load_profile, load_log, get_weight_trend, load_memory
    from progression import weekly_volume, detect_plateaus
    from memory_core import get_recent_episodes

    prof = load_profile() or {}
    wlog = load_log()
    sessions = wlog.get("sessions", [])
    from datetime import timedelta
    from agent_core import today as _today
    cutoff = _today() - timedelta(days=28)
    recent = [s for s in sessions if _safe_date(s.get("date")) and _safe_date(s.get("date")) >= cutoff]
    planned = prof.get("days_per_week") or 6
    weeks = 4
    adherence = f"{len(recent)} sessions in the last 28 days (~{len(recent)/weeks:.1f}/wk vs {planned} planned)"

    # Average recovery over the last week of check-ins/wearable days.
    rec_scores = []
    try:
        from checkin import recovery_score
        for i in range(7):
            d = (_today() - timedelta(days=i)).isoformat()
            score, _ = recovery_score(d, wlog)
            rec_scores.append(score)
    except Exception:
        pass
    rec_line = (f"avg recovery readiness last 7d: {sum(rec_scores)/len(rec_scores):.1f}/10"
                if rec_scores else "no recovery data")

    vol = weekly_volume(wlog)
    plateaus = detect_plateaus(wlog) or ["none"]
    eps = get_recent_episodes(3)
    ep_line = "; ".join(e["summary"][:120] for e in eps) if eps else "none"

    outcomes = ""
    try:
        from feedback import intervention_summary
        outcomes = intervention_summary()
    except Exception:
        pass

    return (
        f"Adherence: {adherence}\n"
        f"Recovery: {rec_line}\n"
        f"Weekly volume: this week {vol['this_week']:,}kg, last week {vol['last_week']:,}kg\n"
        f"Weight trend: {get_weight_trend(load_memory())}\n"
        f"Current plateaus: {'; '.join(plateaus)}\n"
        + (f"{outcomes}\n" if outcomes else "")
        + f"Recent days: {ep_line}"
    )


def _safe_date(s):
    from datetime import datetime
    try:
        return datetime.strptime(s or "", "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def reflect_and_tune() -> str | None:
    """Weekly self-tuning: the LLM reviews real training/recovery data and
    proposes adjustments to the tunable params, each with a reason. Every
    proposal is validated against PARAM_SPECS and clamped before it's applied.
    Returns a short human-readable report, or None if nothing changed."""
    import json as _json
    import re as _re
    from llm import chat

    ctx = _reflection_context()
    current = {k: get_param(k) for k in PARAM_SPECS}
    spec_lines = "\n".join(
        f"- {k}: currently {current[k]}, allowed {s['min']}..{s['max']} ({s['type']}). {s['desc']}"
        for k, s in PARAM_SPECS.items())

    prompt = (
        "You are the self-tuning engine of a personal training app. Review this "
        "user's recent data and decide whether any adaptive parameters should "
        "change. Be conservative: change a value ONLY when the data clearly "
        "supports it, and never move more than one step from the current value. "
        "It is completely fine to change nothing.\n\n"
        f"USER DATA (last ~4 weeks):\n{ctx}\n\n"
        f"TUNABLE PARAMETERS:\n{spec_lines}\n\n"
        "Reasoning examples: chronically low recovery + high streaks -> lower "
        "recovery_fatigue_streak. Repeated plateaus that resolve on their own -> "
        "raise plateau_lookback (more patience). Deloads that leave the user "
        "flat -> gentler deload_week_factor.\n\n"
        "Return ONLY a JSON object mapping the keys you want to change to "
        '{\"value\": <number>, \"reason\": \"<max 16 words>\"}. Return {} to '
        "change nothing. Do not include keys you are not changing."
    )
    try:
        raw = chat([{"role": "system", "content": "You output only strict JSON."},
                    {"role": "user", "content": prompt}], temperature=0.2)
    except Exception as e:
        log.error(f"reflect_and_tune LLM error: {e}")
        return None

    m = _re.search(r"\{.*\}", raw or "", _re.DOTALL)
    if not m:
        return None
    try:
        proposals = _json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(proposals, dict) or not proposals:
        return None

    applied = []
    for key, p in proposals.items():
        if key not in PARAM_SPECS or not isinstance(p, dict):
            continue
        rec = set_param(key, p.get("value"), p.get("reason", "self-tuned from recent data"))
        if rec:
            applied.append(f"{key}: {rec['prev']} -> {rec['value']} ({rec['reason']})")
    if not applied:
        return None
    log.info("reflect_and_tune applied: " + "; ".join(applied))
    return "Self-tuned: " + "; ".join(applied)
