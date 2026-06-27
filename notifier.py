"""
Outbound notifications for scheduled nudges/reports.
Primary channel: Telegram (free, unlimited, no 24h window).
Fallback channel: WhatsApp via Twilio.
"""

import logging
import os

import requests

log = logging.getLogger(__name__)

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TG_API             = "https://api.telegram.org/bot{token}/{method}"
TG_LIMIT           = 4096

# ── WhatsApp (Twilio) ─────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID      = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN       = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM    = os.environ.get("TWILIO_WHATSAPP_FROM", "").strip()
ALLOWED_WHATSAPP_NUMBER = os.environ.get("ALLOWED_WHATSAPP_NUMBER", "").strip()


def download_telegram_file(file_id: str) -> bytes | None:
    """Resolve a Telegram file_id and download its bytes. Returns None on failure."""
    if not (TELEGRAM_BOT_TOKEN and file_id):
        return None
    try:
        r = requests.get(
            TG_API.format(token=TELEGRAM_BOT_TOKEN, method="getFile"),
            params={"file_id": file_id}, timeout=15,
        )
        if r.status_code != 200:
            log.error(f"Telegram getFile failed {r.status_code}: {r.text[:150]}")
            return None
        path = (r.json().get("result") or {}).get("file_path")
        if not path:
            return None
        fr = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{path}", timeout=30,
        )
        if fr.status_code != 200:
            log.error(f"Telegram file download failed {fr.status_code}")
            return None
        return fr.content
    except Exception as e:
        log.error(f"Telegram file download error: {e}", exc_info=True)
        return None


def send_telegram(body: str, chat_id: str | None = None) -> bool:
    """Send a Telegram message. Splits messages over Telegram's 4096-char limit."""
    if not body:
        return False
    chat_id = chat_id or TELEGRAM_CHAT_ID
    if not (TELEGRAM_BOT_TOKEN and chat_id):
        log.warning("Telegram not configured; cannot send.")
        return False
    url = TG_API.format(token=TELEGRAM_BOT_TOKEN, method="sendMessage")
    try:
        for i in range(0, max(len(body), 1), TG_LIMIT):
            r = requests.post(url, json={
                "chat_id": chat_id,
                "text": body[i:i + TG_LIMIT],
                "disable_web_page_preview": True,
            }, timeout=15)
            if r.status_code != 200:
                log.error(f"Telegram send failed {r.status_code}: {r.text[:200]}")
                return False
        return True
    except Exception as e:
        log.error(f"Telegram send error: {e}", exc_info=True)
        return False


def send_telegram_document(content, filename: str, caption: str = "", chat_id: str | None = None) -> bool:
    """Send a file (e.g. a JSON backup) to Telegram."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    chat_id = chat_id or TELEGRAM_CHAT_ID
    if not (TELEGRAM_BOT_TOKEN and chat_id):
        log.warning("Telegram not configured; cannot send document.")
        return False
    url = TG_API.format(token=TELEGRAM_BOT_TOKEN, method="sendDocument")
    try:
        r = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption[:1000]},
            files={"document": (filename, content)},
            timeout=30,
        )
        if r.status_code != 200:
            log.error(f"Telegram sendDocument failed {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"Telegram sendDocument error: {e}", exc_info=True)
        return False


def send_whatsapp(body: str) -> bool:
    """Send an outbound WhatsApp message via Twilio REST. Returns success."""
    if not body:
        return False
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM and ALLOWED_WHATSAPP_NUMBER):
        log.warning("Twilio not fully configured; cannot send outbound WhatsApp.")
        return False
    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        for i in range(0, max(len(body), 1), 1500):
            client.messages.create(
                from_=TWILIO_WHATSAPP_FROM,
                to=ALLOWED_WHATSAPP_NUMBER,
                body=body[i:i + 1500],
            )
        return True
    except Exception as e:
        log.error(f"Failed to send WhatsApp: {e}", exc_info=True)
        return False


def notify(body: str) -> bool:
    """Send via the best available channel — Telegram first, WhatsApp fallback."""
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        return send_telegram(body)
    return send_whatsapp(body)
