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

    lines.append("")
    lines.append("Last runs:")
    for ev, label in [("cron_daily", "Daily nudge"),
                      ("cron_weekly", "Weekly report"),
                      ("cron_check", "Smart alerts"),
                      ("cron_backup", "Backup")]:
        lines.append(f"  {label}: {get_event(ev) or 'never'}")
    return "\n".join(lines)
