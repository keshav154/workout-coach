"""
Daily wellness check-in (sleep / energy / soreness) and a recovery-readiness
score derived from it — no wearable required. Feeds the coach's intensity calls.
"""

import logging

from agent_core import _col, get_consecutive_workout_days, load_log, today_iso

log = logging.getLogger(__name__)


def save_checkin(sleep_hours: float | None = None, energy: int | None = None,
                 soreness: int | None = None, note: str = "", day_str: str | None = None) -> dict:
    day_str = day_str or today_iso()
    data = {}
    if sleep_hours is not None: data["sleep_hours"] = float(sleep_hours)
    if energy is not None:      data["energy"] = int(energy)
    if soreness is not None:    data["soreness"] = int(soreness)
    if note:                    data["note"] = note
    if not data:
        return {}
    _col("checkin").update_one({"_id": day_str}, {"$set": data}, upsert=True)
    data["date"] = day_str
    return data


def get_checkin(day_str: str | None = None) -> dict | None:
    day_str = day_str or today_iso()
    doc = _col("checkin").find_one({"_id": day_str})
    if doc:
        doc.pop("_id", None)
        return doc
    return None


def recovery_score(day_str: str | None = None, log: dict | None = None) -> tuple[int, list[str]]:
    """1-10 readiness from today's check-in (sleep, energy, soreness) + training streak."""
    day_str = day_str or today_iso()
    log = log or load_log()
    score, reasons = 7, []

    c = get_checkin(day_str) or {}
    sleep = c.get("sleep_hours")
    energy = c.get("energy")
    soreness = c.get("soreness")

    if sleep is not None:
        if sleep >= 7.5:   score += 1
        elif sleep >= 6.5: pass
        elif sleep >= 5.5: score -= 1; reasons.append(f"short sleep ({sleep}h)")
        else:              score -= 2; reasons.append(f"poor sleep ({sleep}h)")
    if energy is not None:        # 1-10 self-rating
        if energy >= 8:   score += 1
        elif energy <= 4: score -= 2; reasons.append(f"low energy ({energy}/10)")
        elif energy <= 6: score -= 1
    if soreness is not None:      # 1-10, higher = more sore
        if soreness >= 8: score -= 2; reasons.append(f"very sore ({soreness}/10)")
        elif soreness >= 6: score -= 1; reasons.append("notable soreness")

    streak = get_consecutive_workout_days(log)
    if streak >= 6:   score -= 2; reasons.append(f"{streak} days training in a row")
    elif streak >= 4: score -= 1

    return max(1, min(10, score)), reasons


def format_checkin_block(day_str: str | None = None, log: dict | None = None) -> str:
    day_str = day_str or today_iso()
    c = get_checkin(day_str)
    score, reasons = recovery_score(day_str, log)
    lines = []
    if c:
        bits = []
        if c.get("sleep_hours") is not None: bits.append(f"{c['sleep_hours']}h sleep")
        if c.get("energy")      is not None: bits.append(f"energy {c['energy']}/10")
        if c.get("soreness")    is not None: bits.append(f"soreness {c['soreness']}/10")
        if bits:
            lines.append("TODAY'S CHECK-IN: " + " | ".join(bits))
    lines.append(f"RECOVERY READINESS: {score}/10" +
                 (f" ({', '.join(reasons)})" if reasons else ""))
    return "\n".join(lines)


def parse_checkin_command(text: str) -> dict | None:
    """
    Accept '!checkin 7 8 3' (sleep energy soreness) or
    '!checkin sleep=7 energy=8 soreness=3'.
    """
    body = text[len("!checkin"):].strip()
    if not body:
        return None
    kv = {}
    if "=" in body:
        for tok in body.replace(",", " ").split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                kv[k.strip().lower()] = v.strip()
        try:
            return {
                "sleep_hours": float(kv["sleep"]) if "sleep" in kv else None,
                "energy":      int(float(kv["energy"])) if "energy" in kv else None,
                "soreness":    int(float(kv["soreness"])) if "soreness" in kv else None,
            }
        except (ValueError, KeyError):
            return None
    # positional: sleep energy soreness
    parts = body.split()
    try:
        out = {}
        if len(parts) >= 1: out["sleep_hours"] = float(parts[0])
        if len(parts) >= 2: out["energy"]      = int(float(parts[1]))
        if len(parts) >= 3: out["soreness"]    = int(float(parts[2]))
        return out or None
    except ValueError:
        return None
