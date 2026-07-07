"""
Self-monitoring: DB/health checks, cron heartbeats, status, and admin alerts.
The agent watches itself and pings you on Telegram when something breaks.
"""

import logging
import os
import time
from datetime import datetime

from agent_core import _col, load_log
from notifier import send_telegram

log = logging.getLogger(__name__)

_START_TS  = time.time()
ADMIN_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def db_ok() -> bool:
    try:
        _col("system").database.client.admin.command("ping")
        return True
    except Exception as e:
        log.error(f"DB ping failed: {e}")
        return False


def record_event(name: str) -> None:
    """Heartbeat — record the last time a job (e.g. a cron) ran."""
    try:
        _col("system").update_one(
            {"_id": name},
            {"$set": {"last": datetime.utcnow().isoformat()}},
            upsert=True,
        )
    except Exception as e:
        log.error(f"record_event({name}) failed: {e}")


def get_event(name: str) -> str | None:
    try:
        d = _col("system").find_one({"_id": name})
        return d.get("last") if d else None
    except Exception:
        return None


def alert_admin(text: str) -> bool:
    """Send an operational alert to the owner's Telegram."""
    if not ADMIN_CHAT:
        return False
    return send_telegram(f"⚠️ CoachX alert:\n{text}", ADMIN_CHAT)


def tool_usage_stats(n: int = 20) -> dict:
    """How often the coach's recent turns actually called a real data tool,
    vs. answered from context/guess alone (or the tool loop wasn't supported
    by the provider and fell back)."""
    try:
        docs = list(_col("tool_usage").find().sort("_id", -1).limit(n))
    except Exception:
        return {"count": 0}
    if not docs:
        return {"count": 0}
    used = sum(1 for d in docs if d.get("used_tools"))
    return {"count": len(docs), "used": used, "rate": round(100 * used / len(docs))}


def get_status() -> str:
    up = int(time.time() - _START_TS)
    h, m = up // 3600, (up % 3600) // 60
    lines = ["CoachX status", f"Uptime: {h}h {m}m"]

    ok = db_ok()
    lines.append(f"Database: {'OK' if ok else 'DOWN'}")
    if ok:
        try:
            lines.append(f"Sessions logged: {len(load_log().get('sessions', []))}")
        except Exception as e:
            lines.append(f"DB read error: {e}")

    stats = tool_usage_stats()
    if stats["count"]:
        lines.append(f"Tool-call usage (last {stats['count']} data-driven turns): "
                     f"{stats['used']}/{stats['count']} ({stats['rate']}%) actually fetched real data")
        if stats["rate"] < 50:
            lines.append("  ⚠️ Low tool-call rate — your model/provider may not support "
                         "function calling. Answers are likely falling back to context-only reasoning.")
    else:
        lines.append("Tool-call usage: no data yet — ask a data question (e.g. "
                     "'what's my best bench press?') to generate a sample.")

    lines.append("")
    lines.append("Last runs:")
    for ev, label in [("cron_daily", "Daily nudge"),
                      ("cron_weekly", "Weekly report"),
                      ("cron_check", "Smart alerts"),
                      ("cron_backup", "Backup"),
                      ("cron_selfheal", "Self-heal"),
                      ("cron_plateau", "Plateau check"),
                      ("cron_evening", "Evening check-in")]:
        lines.append(f"  {label}: {get_event(ev) or 'never'}")
    return "\n".join(lines)
