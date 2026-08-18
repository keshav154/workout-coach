"""
Outcome feedback loop — the app grades its OWN autonomous decisions and learns
from what actually worked, instead of only adapting to raw data.

When the coach makes an intervention (auto-deload a plateaued lift, recalibrate
maintenance calories, nudge the calorie target), it records the metric it was
trying to move. Later, evaluate_interventions() checks whether that metric
actually improved and distils the pattern into a durable coach_observation and
a summary the weekly self-tuner reads. That's the difference between
self-ADJUSTING (react to data) and self-IMPROVING (react to your own results).
"""

import logging
from datetime import datetime

from agent_core import (_col, apply_memory_update, get_weight_entries,
                        load_log, load_memory, save_memory, today, today_iso)

log = logging.getLogger(__name__)

HORIZONS = {"deload": 14, "calorie": 21}      # days before an outcome is judged


def record_intervention(kind: str, key: str, baseline, meta: dict | None = None) -> bool:
    """Log an autonomous decision to grade later. No-ops if an un-evaluated
    intervention of the same kind+key is already open (don't double-count)."""
    existing = _col("interventions").find_one(
        {"kind": kind, "key": key, "evaluated": False})
    if existing:
        return False
    _col("interventions").insert_one({
        "kind": kind, "key": key, "baseline": baseline,
        "date": today_iso(), "meta": meta or {}, "evaluated": False,
        "outcome": None,
    })
    return True


def _days_since(date_s: str) -> int:
    try:
        return (today() - datetime.strptime(date_s, "%Y-%m-%d").date()).days
    except (ValueError, TypeError):
        return 0


def _best_e1rm_since(name: str, since: str) -> float | None:
    """Best estimated-1RM for `name` in sessions strictly after `since`."""
    from progression import epley_1rm
    from agent_core import _num
    best = None
    for s in load_log().get("sessions", []):
        if (s.get("date") or "") <= since:
            continue
        for e in s.get("exercises", []):
            if e.get("name") == name:
                sets = e.get("sets")
                if isinstance(sets, list) and sets:
                    cand = max(epley_1rm(_num(x.get("weight")), _num(x.get("reps"))) for x in sets)
                else:
                    cand = epley_1rm(_num(e.get("weight")), _num(e.get("reps_done")))
                if best is None or cand > best:
                    best = cand
    return best


def _measure(doc: dict) -> str:
    """Grade one intervention: 'improved' | 'worse' | 'inconclusive'."""
    kind = doc.get("kind")
    if kind == "deload":
        after = _best_e1rm_since(doc["key"], doc["date"])
        if after is None:
            return "inconclusive"                 # lift not trained since
        base = float(doc.get("baseline") or 0)
        if base <= 0:
            return "inconclusive"
        return "improved" if after > base * 1.01 else "worse"

    if kind == "calorie":
        base = doc.get("baseline") or {}
        start_w = base.get("weight")
        goal = (base.get("goal") or "").lower()
        entries = [w for d, w in get_weight_entries(load_memory())
                   if d > doc["date"]]
        if start_w is None or not entries:
            return "inconclusive"
        change = entries[-1] - float(start_w)
        if any(k in goal for k in ("lose", "fat", "cut")):
            return "improved" if change < -0.2 else "worse"
        if any(k in goal for k in ("gain", "muscle", "bulk")):
            return "improved" if change > 0.2 else "worse"
        return "improved" if abs(change) < 0.6 else "worse"   # recomp/maintain

    return "inconclusive"


_LABELS = {
    "deload": ("Auto-deloads", "cleared the plateau"),
    "calorie": ("Calorie adjustments", "moved your weight toward your goal"),
}


def evaluate_interventions() -> str | None:
    """Grade every due, un-evaluated intervention; write pattern observations to
    memory. Returns a short report, or None if nothing was ready."""
    due = [d for d in _col("interventions").find({"evaluated": False})
           if _days_since(d.get("date", "")) >= HORIZONS.get(d.get("kind"), 14)]
    if not due:
        return None

    tally: dict[str, list[str]] = {}
    for d in due:
        outcome = _measure(d)
        _col("interventions").update_one(
            {"_id": d["_id"]},
            {"$set": {"evaluated": True, "outcome": outcome,
                      "evaluated_date": today_iso()}})
        tally.setdefault(d.get("kind"), []).append(outcome)

    mem = load_memory()
    reports, observations = [], []
    for kind, outcomes in tally.items():
        graded = [o for o in outcomes if o in ("improved", "worse")]
        if not graded:
            continue
        wins = graded.count("improved")
        noun, effect = _LABELS.get(kind, (kind, "helped"))
        obs = f"{noun} {effect} {wins}/{len(graded)} of the last times tried."
        observations.append(obs)
        reports.append(obs)

    if observations:
        apply_memory_update(mem, {"coach_observations": observations})
        save_memory(mem)
    return ("Graded " + str(len(due)) + " past decision(s): " + " ".join(reports)
            if reports else None)


def intervention_summary(limit: int = 8) -> str:
    """Recent graded outcomes, for the self-tuner's context (and transparency)."""
    rows = [d for d in _col("interventions").find({"evaluated": True})
            if d.get("outcome") in ("improved", "worse")]
    rows.sort(key=lambda d: d.get("evaluated_date", ""), reverse=True)
    rows = rows[:limit]
    if not rows:
        return ""
    by_kind: dict[str, list[str]] = {}
    for d in rows:
        by_kind.setdefault(d["kind"], []).append(d["outcome"])
    parts = []
    for kind, outs in by_kind.items():
        noun = _LABELS.get(kind, (kind, ""))[0]
        parts.append(f"{noun}: worked {outs.count('improved')}/{len(outs)}")
    return "Intervention outcomes — " + "; ".join(parts)
