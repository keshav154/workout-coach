"""
Trust layer: validation, an audit trail, universal undo, and data export.
Philosophy: optimistic writes, but every write is validated and reversible.
"""

import json
import logging
from datetime import datetime, timezone

from bson import ObjectId

from agent_core import _col, today

log = logging.getLogger(__name__)

BACKUP_COLLECTIONS = ["profile", "workout_log", "memory", "expenses", "budget",
                      "history", "checkin", "goals", "audit", "meals",
                      "episodes", "lessons", "measurements", "habits",
                      "habit_log", "photos", "rest_days", "water", "program"]


# ── Validation ────────────────────────────────────────────────────────────────
def validate_session(s: dict) -> tuple[bool, str, dict]:
    """Sanity-check a parsed session. Returns (ok, reason_if_not, cleaned)."""
    s = dict(s)
    now = today()

    # Date: never future, never garbage — default to today
    d = s.get("date")
    if not d or d == "YYYY-MM-DD":
        s["date"] = now.isoformat()
    else:
        try:
            if datetime.strptime(d, "%Y-%m-%d").date() > now:
                s["date"] = now.isoformat()
        except ValueError:
            s["date"] = now.isoformat()

    bw = s.get("body_weight_kg")
    if bw:
        try:
            bw = float(bw)
            if not (30 <= bw <= 300):
                return False, f"Body weight {bw} kg looks wrong, so I didn't log this session.", s
        except (TypeError, ValueError):
            s["body_weight_kg"] = None

    cal = (s.get("nutrition") or {}).get("calories_eaten")
    if cal:
        try:
            if float(cal) > 12000:
                return False, "Calories over 12000 looks wrong, so I didn't log this session.", s
        except (TypeError, ValueError):
            pass

    return True, "", s


def validate_expense(amount: float) -> tuple[bool, str]:
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return False, "I couldn't read the amount."
    if amount <= 0:
        return False, "Amount must be positive."
    if amount > 1_000_000:
        return False, "That amount looks too large, so I didn't log it."
    return True, ""


# ── Audit trail + undo ────────────────────────────────────────────────────────
def record_audit(kind: str, summary: str, ref=None) -> None:
    _col("audit").insert_one({
        "ts":      datetime.now(timezone.utc).isoformat(),
        "kind":    kind,
        "summary": summary,
        "ref":     ref,
    })


def last_audit_summary(n: int = 3) -> list[str]:
    docs = list(_col("audit").find().sort("_id", -1).limit(n))
    return [f"{d.get('kind')}: {d.get('summary')}" for d in docs]


def _delete_by_id(collection: str, ref) -> None:
    try:
        _col(collection).delete_one({"_id": ObjectId(ref)})
    except Exception:
        _col(collection).delete_one({"_id": ref})


def undo_last() -> str:
    doc = _col("audit").find_one(sort=[("_id", -1)])
    if not doc:
        return "Nothing to undo."
    kind = doc.get("kind")
    ref  = doc.get("ref")
    summary = doc.get("summary", "")

    if kind == "session":
        if isinstance(ref, dict) and ref.get("date"):
            # Remove the exact audited session (dedup means at most one match).
            _col("workout_log").update_one(
                {"_id": "log"},
                {"$pull": {"sessions": {"date": ref["date"], "day": ref.get("day")}}},
            )
        else:
            _col("workout_log").update_one({"_id": "log"}, {"$pop": {"sessions": 1}})
    elif kind == "expense" and ref:
        _delete_by_id("expenses", ref)
    elif kind == "meal" and ref:
        _delete_by_id("meals", ref)
    elif kind == "measurement" and ref:
        _delete_by_id("measurements", ref)
    elif kind == "weight" and isinstance(ref, dict) and ref.get("entry"):
        mem = _col("memory").find_one({"_id": "mem"}) or {}
        wl = [e for e in mem.get("weight_log", []) if e != ref["entry"]]
        _col("memory").update_one({"_id": "mem"}, {"$set": {"weight_log": wl}}, upsert=True)
        if ref.get("prev_kg"):
            _col("profile").update_one({"_id": "user"},
                                       {"$set": {"weight_kg": ref["prev_kg"]}})
    else:
        return f"The last action ({kind}) can't be undone automatically."

    _col("audit").delete_one({"_id": doc["_id"]})
    return f"Undone: {summary}"


# ── Backup / export ───────────────────────────────────────────────────────────
def export_all() -> str:
    """Dump every collection to a JSON string for backup."""
    out = {}
    for name in BACKUP_COLLECTIONS:
        docs = list(_col(name).find())
        for d in docs:
            d["_id"] = str(d.get("_id"))
        out[name] = docs
    out["_exported_at"] = datetime.now(timezone.utc).isoformat()
    return json.dumps(out, indent=2, default=str)
