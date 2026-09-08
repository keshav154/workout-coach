"""
Per-muscle freshness / recovery model → smart 'what to train today'.

Fitbod's core paid feature: instead of only following a fixed rotation, model
how recovered each muscle group is (from how recently and how hard it was
trained), then recommend the program day that hits the FRESHEST muscles and
avoids ones still under fatigue.

Freshness is a 0-100 score per muscle: it drops with training load and recovers
linearly over a muscle-specific number of days. Deterministic — no LLM, so it's
free and never rate-limited. It augments the rotation; it never overrides a
deliberate rest day.
"""

import logging
from datetime import datetime

from agent_core import get_program, load_log, today
from muscles import muscle_groups, LANDMARKS, _prescribed_sets, _sets_in

log = logging.getLogger(__name__)

# Days to fully recover a muscle from a hard session (larger muscles recover
# slower). Freshness climbs linearly back to 100 over this window.
RECOVERY_DAYS = {
    "Chest": 2.5, "Back": 3.0, "Legs": 3.5, "Shoulders": 2.0,
    "Biceps": 2.0, "Triceps": 2.0, "Core": 1.5,
}
_FATIGUE_PER_SET = 12.0        # freshness points a muscle loses per hard set


def muscle_freshness(log: dict | None = None) -> dict[str, dict]:
    """0-100 freshness per muscle. 100 = fully recovered/untrained recently,
    lower = still fatigued. Considers the last ~5 days of sessions."""
    log = log or load_log()
    now = today()
    prescribed = _prescribed_sets()

    # Accumulate residual fatigue from recent sessions, decayed by recovery.
    fatigue = {m: 0.0 for m in LANDMARKS}
    last_trained: dict[str, str] = {}
    for s in log.get("sessions", []):
        try:
            d = datetime.strptime(s.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        days_ago = (now - d).days
        if days_ago < 0 or days_ago > 6:
            continue
        for ex in s.get("exercises", []):
            n_sets = _sets_in(ex, prescribed)
            for m in muscle_groups(ex.get("name", "")):
                if m not in fatigue:
                    continue
                recov = RECOVERY_DAYS.get(m, 2.5)
                # Fraction of this session's fatigue still lingering today.
                remaining = max(0.0, 1.0 - days_ago / recov)
                fatigue[m] += n_sets * _FATIGUE_PER_SET * remaining
                cur = last_trained.get(m)
                if cur is None or s.get("date", "") > cur:
                    last_trained[m] = s.get("date", "")

    out = {}
    for m in LANDMARKS:
        score = max(0, min(100, round(100 - fatigue[m])))
        state = "fresh" if score >= 70 else "moderate" if score >= 40 else "fatigued"
        out[m] = {"score": score, "state": state,
                  "last_trained": last_trained.get(m)}
    return out


def _day_muscles(day_key: str) -> set[str]:
    prog = get_program().get(day_key, {})
    ms = set()
    for ex in prog.get("exercises", []):
        ms.update(muscle_groups(ex.get("name", "")))
    return ms


def recommend_day(log: dict | None = None) -> dict:
    """Pick the program day whose muscles are freshest right now. Returns
    {recommended, scheduled, freshness, day_scores, agrees, reason}. `scheduled`
    is the normal rotation's next day; `agrees` says whether they match."""
    from agent_core import get_next_day, get_rotation, get_program as _gp
    log = log or load_log()
    fresh = muscle_freshness(log)
    scheduled = get_next_day(log)

    scores = []
    for day_key in get_rotation():
        ms = _day_muscles(day_key)
        if not ms:
            continue
        avg = round(sum(fresh[m]["score"] for m in ms if m in fresh)
                    / max(1, len([m for m in ms if m in fresh])))
        scores.append({"day": day_key, "name": _gp().get(day_key, {}).get("name", ""),
                       "avg_freshness": avg,
                       "muscles": sorted(ms)})
    scores.sort(key=lambda r: -r["avg_freshness"])
    if not scores:
        return {"recommended": scheduled, "scheduled": scheduled,
                "freshness": fresh, "day_scores": [], "agrees": True,
                "reason": "No program days to compare."}

    best = scores[0]
    agrees = best["day"] == scheduled
    if agrees:
        reason = f"Day {scheduled} is both next in rotation and hits fresh muscles ({best['avg_freshness']}/100)."
    else:
        sched_row = next((s for s in scores if s["day"] == scheduled), None)
        sched_fr = sched_row["avg_freshness"] if sched_row else "?"
        reason = (f"Day {best['day']} ({best['name']}) is fresher "
                  f"({best['avg_freshness']}/100) than the scheduled Day {scheduled} "
                  f"({sched_fr}/100) — its muscles are more recovered today.")
    return {"recommended": best["day"], "scheduled": scheduled,
            "freshness": fresh, "day_scores": scores, "agrees": agrees,
            "reason": reason}


def format_freshness_block(log: dict | None = None) -> str:
    """Prompt block so the coach can factor recovery into what it suggests."""
    rec = recommend_day(log)
    fatigued = [m for m, d in rec["freshness"].items() if d["state"] == "fatigued"]
    if rec["agrees"] and not fatigued:
        return ""
    lines = ["MUSCLE FRESHNESS (per-muscle recovery today):"]
    if fatigued:
        lines.append("- Still fatigued (train lighter or skip): " + ", ".join(fatigued))
    if not rec["agrees"]:
        lines.append(f"- {rec['reason']} Consider suggesting Day {rec['recommended']} "
                     f"if the user is flexible, but respect their rotation preference.")
    return "\n".join(lines)
