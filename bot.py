"""
CoachX — Flask web UI + WhatsApp (Twilio) + Discord bot on Render.
All message routing lives in messaging.process_message; this file is just transport.
Data persisted in MongoDB Atlas.
"""

import functools
import logging
import os
import threading

import discord
from flask import Flask, jsonify, render_template, request

from agent_core import (
    PROGRAM,
    get_next_day,
    load_history,
    load_log,
    load_memory,
    load_profile,
    profile_complete,
    reset_history,
)
from messaging import (
    HELP_MSG,
    analyze_meal_photo,
    process_message,
    transcribe_and_process,
)
from reports import build_daily_nudge, build_weekly_report
from notifier import download_telegram_file, notify, send_telegram, send_telegram_document
from alerts import run_checks
from monitor import alert_admin, get_status, record_event
from trust import export_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "").strip()
FLASK_SECRET    = os.environ.get("FLASK_SECRET", "change-me").strip()
WEB_PASSWORD    = os.environ.get("WEB_PASSWORD", "").strip()
CRON_SECRET     = os.environ.get("CRON_SECRET", "").strip()

TWILIO_AUTH_TOKEN       = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
ALLOWED_WHATSAPP_NUMBER = os.environ.get("ALLOWED_WHATSAPP_NUMBER", "").strip()

TELEGRAM_CHAT_ID        = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()


def require_auth(f):
    """Check X-Password header sent by the JS frontend on every API call."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not WEB_PASSWORD:
            return f(*args, **kwargs)
        if request.path == "/" or (request.method == "GET" and request.path in ("/health",)):
            return f(*args, **kwargs)
        pwd = request.headers.get("X-Password", "")
        if pwd == WEB_PASSWORD:
            return f(*args, **kwargs)
        return jsonify({"error": "unauthorized"}), 401
    return decorated


# ── Flask web app ──────────────────────────────────────────────────────────────
flask_app = Flask(__name__)
flask_app.secret_key = FLASK_SECRET


@flask_app.route("/")
@require_auth
def index():
    return render_template("index.html")


# ── PWA: manifest, service worker, icon (served openly so the app can install) ─
@flask_app.route("/manifest.webmanifest")
def manifest():
    return jsonify({
        "name": "CoachX",
        "short_name": "CoachX",
        "description": "Your personal AI fitness & finance coach",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0a0a0f",
        "theme_color": "#0a0a0f",
        "icons": [
            {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml",
             "purpose": "any maskable"},
        ],
    }), 200, {"Content-Type": "application/manifest+json"}


@flask_app.route("/sw.js")
def service_worker():
    js = """
const CACHE = 'coachx-v2';
self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(c => c.add('/')));
});
self.addEventListener('activate', e => {
  e.waitUntil(Promise.all([
    caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))),
    self.clients.claim(),
  ]));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                 // never cache POSTs (chat/log)
  if (req.mode === 'navigate') {                    // app shell: network, fallback to cache
    e.respondWith(fetch(req).catch(() => caches.match('/')));
  }
});
""".strip()
    return js, 200, {"Content-Type": "application/javascript"}


@flask_app.route("/icon.svg")
def app_icon():
    svg = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#6c63ff"/>
      <stop offset="1" stop-color="#ff6584"/>
    </linearGradient>
  </defs>
  <rect width="512" height="512" rx="112" fill="url(#g)"/>
  <path d="M286 80 154 300h84l-28 132 132-220h-84z" fill="#fff"/>
</svg>
""".strip()
    return svg, 200, {"Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400"}


@flask_app.route("/chat", methods=["POST"])
@require_auth
def chat_route():
    user_text = (request.json or {}).get("message", "").strip()
    if not user_text:
        return jsonify({"error": "empty message"}), 400
    try:
        reply = process_message(user_text, source="web")
    except Exception as e:
        log.error(f"Web chat error: {e}", exc_info=True)
        return jsonify({"error": "Something went wrong, please try again"}), 500
    return jsonify({"reply": reply})


@flask_app.route("/reset", methods=["POST"])
@require_auth
def reset_web():
    reset_history("web")
    reset_history("web_expense")
    return jsonify({"ok": True})


@flask_app.route("/chat_history")
@require_auth
def chat_history():
    return jsonify({"history": load_history("web")})


@flask_app.route("/day_info")
@require_auth
def day_info():
    profile = load_profile()
    if not profile_complete(profile):
        return jsonify({"day": "?", "name": "Setup", "focus": "Profile setup in progress"})
    workout_log = load_log()
    day = get_next_day(workout_log)
    p   = PROGRAM.get(day, {})
    return jsonify({"day": day, "name": p.get("name", ""), "focus": p.get("focus", "")})


@flask_app.route("/stats")
@require_auth
def stats():
    from datetime import date, timedelta
    workout_log = load_log()
    mem         = load_memory()
    sessions    = workout_log.get("sessions", [])
    day         = get_next_day(workout_log)
    p           = PROGRAM.get(day, {})

    weight_entries = mem.get("weight_log", [])
    last_weight = None
    if weight_entries:
        try:
            last_weight = float(sorted(weight_entries)[-1].split(": ")[1].replace(" kg", ""))
        except Exception:
            pass

    today      = date.today()
    week_start = today - timedelta(days=today.weekday())
    sessions_this_week = sum(
        1 for s in sessions if s.get("date", "") >= week_start.isoformat()
    )

    recent = []
    for s in reversed(sessions[-5:]):
        d = s.get("day", "?")
        recent.append({
            "day":       d,
            "name":      PROGRAM.get(d, {}).get("name", ""),
            "date":      s.get("date", ""),
            "weight":    s.get("body_weight_kg"),
            "exercises": len(s.get("exercises", [])),
        })

    return jsonify({
        "total_sessions":     len(sessions),
        "last_weight":        last_weight,
        "next_day":           day,
        "next_name":          p.get("name", ""),
        "sessions_this_week": sessions_this_week,
        "recent_sessions":    recent,
    })


@flask_app.route("/today_program")
@require_auth
def today_program():
    from agent_core import get_last_session_for_day
    profile = load_profile()
    if not profile_complete(profile):
        return jsonify({"ready": False})
    workout_log = load_log()
    day  = get_next_day(workout_log)
    p    = PROGRAM.get(day, {})
    last = get_last_session_for_day(workout_log, day)
    exercises = []
    for ex in p.get("exercises", []):
        prev = None
        if last:
            prev = next((e for e in last.get("exercises", []) if e["name"] == ex["name"]), None)
        exercises.append({
            "name":        ex["name"],
            "sets":        ex["sets"],
            "rep_range":   ex["rep_range"],
            "scheme":      ex.get("scheme"),
            "last_weight": prev.get("weight") if prev else None,
            "last_reps":   prev.get("reps_done") if prev else None,
        })
    return jsonify({"ready": True, "day": day, "name": p.get("name", ""), "exercises": exercises})


@flask_app.route("/log_workout", methods=["POST"])
@require_auth
def log_workout():
    from datetime import date as _date
    from agent_core import (apply_memory_update, detect_prs, load_memory,
                            save_memory, save_session)
    from trust import record_audit, validate_session

    data      = request.json or {}
    day       = data.get("day")
    exercises = [e for e in data.get("exercises", []) if e.get("weight") or e.get("reps_done")]
    if not day or not exercises:
        return jsonify({"error": "Nothing to log — fill in at least one exercise."}), 400

    session = {"day": day, "date": _date.today().isoformat(), "exercises": exercises}
    if data.get("body_weight_kg"):
        session["body_weight_kg"] = data["body_weight_kg"]

    ok, reason, cleaned = validate_session(session)
    if not ok:
        return jsonify({"error": reason}), 400

    workout_log = load_log()
    prs = detect_prs(workout_log, cleaned)
    save_session(workout_log, cleaned)
    record_audit("session", f"Day {day} (workout mode) on {cleaned['date']}")
    if prs:
        mem = load_memory()
        apply_memory_update(mem, {"personal_records": prs})
        save_memory(mem)
    return jsonify({"ok": True, "prs": prs})


@flask_app.route("/profile_status")
@require_auth
def profile_status():
    return jsonify({"complete": profile_complete(load_profile())})


@flask_app.route("/reset_profile", methods=["POST"])
@require_auth
def reset_profile():
    from agent_core import _col
    _col("profile").delete_one({"_id": "user"})
    reset_history("web")
    reset_history("web_expense")
    reset_history("discord")
    reset_history("whatsapp")
    return jsonify({"ok": True})


@flask_app.route("/delete_last_session", methods=["POST"])
@require_auth
def delete_last_session():
    from agent_core import _col
    result = _col("workout_log").update_one({"_id": "log"}, {"$pop": {"sessions": 1}})
    return jsonify({"ok": True, "modified": result.modified_count})


@flask_app.route("/health")
def health():
    return "OK", 200


@flask_app.route("/status")
@require_auth
def status_route():
    return jsonify({"status": get_status()})


@flask_app.errorhandler(500)
def on_500(e):
    try:
        alert_admin(f"500 on {request.method} {request.path}: {e}")
    except Exception:
        pass
    return jsonify({"error": "internal error"}), 500


# ── Scheduled jobs (hit by an external cron with ?secret=) ─────────────────────
def _cron_authorized() -> bool:
    if not CRON_SECRET:
        return True  # not configured -> allow (set CRON_SECRET to lock down)
    return request.args.get("secret", "") == CRON_SECRET


@flask_app.route("/cron/daily", methods=["GET", "POST"])
def cron_daily():
    if not _cron_authorized():
        return "forbidden", 403
    record_event("cron_daily")
    msg = build_daily_nudge()
    sent = notify(msg) if msg else False
    log.info(f"Daily cron: nudge={'sent' if sent else 'skipped'}")
    return jsonify({"sent": sent, "message": msg})


@flask_app.route("/cron/weekly", methods=["GET", "POST"])
def cron_weekly():
    if not _cron_authorized():
        return "forbidden", 403
    record_event("cron_weekly")
    msg  = build_weekly_report()
    sent = notify(msg)
    log.info(f"Weekly cron: report={'sent' if sent else 'failed'}")
    return jsonify({"sent": sent, "message": msg})


@flask_app.route("/cron/check", methods=["GET", "POST"])
def cron_check():
    if not _cron_authorized():
        return "forbidden", 403
    record_event("cron_check")
    try:
        msgs = run_checks()
    except Exception as e:
        log.error(f"Smart-check cron failed: {e}", exc_info=True)
        alert_admin(f"Smart-check cron failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
    for m in msgs:
        notify(m)
    log.info(f"Smart-check cron: {len(msgs)} alert(s) sent")
    return jsonify({"sent": len(msgs), "alerts": msgs})


@flask_app.route("/cron/backup", methods=["GET", "POST"])
def cron_backup():
    if not _cron_authorized():
        return "forbidden", 403
    record_event("cron_backup")
    from datetime import datetime
    try:
        dump = export_all()
        fname = f"coachx_backup_{datetime.utcnow().strftime('%Y%m%d')}.json"
        sent = send_telegram_document(dump, fname, caption="CoachX weekly backup")
        log.info(f"Backup cron: {'sent' if sent else 'failed'} ({len(dump)} bytes)")
        return jsonify({"sent": sent, "bytes": len(dump)})
    except Exception as e:
        log.error(f"Backup cron failed: {e}", exc_info=True)
        alert_admin(f"Backup failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── WhatsApp webhook (Twilio) ──────────────────────────────────────────────────
@flask_app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    from twilio.twiml.messaging_response import MessagingResponse
    from twilio.request_validator import RequestValidator

    if TWILIO_AUTH_TOKEN:
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        signature = request.headers.get("X-Twilio-Signature", "")
        if not validator.validate(request.url, request.form, signature):
            log.warning("WhatsApp: invalid Twilio signature")
            return "Forbidden", 403

    from_number = request.form.get("From", "")
    user_text   = request.form.get("Body", "").strip()

    if ALLOWED_WHATSAPP_NUMBER and from_number != ALLOWED_WHATSAPP_NUMBER:
        log.warning(f"WhatsApp: ignoring message from {from_number}")
        return str(MessagingResponse())

    log.info(f"WhatsApp message from {from_number}: {user_text[:50]}")

    twiml = MessagingResponse()
    try:
        reply = process_message(user_text, source="whatsapp")
    except Exception as e:
        log.error(f"WhatsApp error: {e}", exc_info=True)
        twiml.message("Something went wrong. Please try again.")
        return str(twiml)

    for i in range(0, max(len(reply), 1), 1500):
        twiml.message(reply[i:i + 1500])
    return str(twiml)


# ── Telegram webhook ───────────────────────────────────────────────────────────
@flask_app.route("/telegram", methods=["POST"])
def telegram_webhook():
    # Verify the secret token Telegram echoes back (set via setWebhook)
    if TELEGRAM_WEBHOOK_SECRET:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if got != TELEGRAM_WEBHOOK_SECRET:
            log.warning("Telegram: bad webhook secret")
            return "forbidden", 403

    update = request.get_json(silent=True) or {}
    msg    = update.get("message") or update.get("edited_message")
    if not msg:
        return jsonify({"ok": True})

    chat_id = str(msg.get("chat", {}).get("id", ""))

    # Lock to the owner's chat once TELEGRAM_CHAT_ID is configured
    if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
        log.warning(f"Telegram: ignoring chat {chat_id}, expected {TELEGRAM_CHAT_ID}")
        return jsonify({"ok": True})

    voice = msg.get("voice") or msg.get("audio")
    photo = msg.get("photo")

    try:
        if voice:
            log.info(f"Telegram voice note from chat {chat_id}")
            data = download_telegram_file(voice.get("file_id"))
            reply = transcribe_and_process(data, source="telegram") if data \
                else "Couldn't download that voice note."
        elif photo:
            log.info(f"Telegram photo from chat {chat_id}")
            largest = photo[-1]  # last entry is the highest resolution
            data    = download_telegram_file(largest.get("file_id"))
            caption = (msg.get("caption") or "").strip()
            reply = analyze_meal_photo(data, caption, source="telegram") if data \
                else "Couldn't download that photo."
        else:
            text = (msg.get("text") or "").strip()
            log.info(f"Telegram message from chat {chat_id}: {text[:50]}")
            if not text:
                return jsonify({"ok": True})
            reply = process_message(text, source="telegram")
    except Exception as e:
        log.error(f"Telegram error: {e}", exc_info=True)
        reply = "Something went wrong. Please try again."

    send_telegram(reply, chat_id)
    return jsonify({"ok": True})


# ── Discord bot ────────────────────────────────────────────────────────────────
def make_discord_client():
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        log.info(f"Discord bot logged in as {client.user}")

    @client.event
    async def on_message(message: discord.Message):
        if message.author == client.user:
            return
        log.info(f"Discord message from {message.author} (id={message.author.id}): {message.content[:50]}")
        if DISCORD_USER_ID and str(message.author.id) != str(DISCORD_USER_ID):
            log.warning(f"Discord: ignoring message from {message.author.id}, expected {DISCORD_USER_ID}")
            return

        text = message.content.strip()
        if not text:
            return

        async with message.channel.typing():
            try:
                reply = process_message(text, source="discord")
            except Exception as e:
                log.error(f"Discord error: {e}", exc_info=True)
                await message.channel.send("Something went wrong. Please try again.")
                return

        for i in range(0, max(len(reply), 1), 1900):
            await message.channel.send(reply[i:i + 1900])

    return client


# ── Start services ─────────────────────────────────────────────────────────────
def run_discord():
    if not DISCORD_TOKEN:
        log.warning("DISCORD_BOT_TOKEN not set - Discord bot disabled.")
        return
    try:
        log.info(f"Starting Discord bot, token starts with: {DISCORD_TOKEN[:10]}...")
        client = make_discord_client()
        client.run(DISCORD_TOKEN)
    except Exception as e:
        log.error(f"Discord bot crashed: {e}", exc_info=True)


if __name__ == "__main__":
    threading.Thread(target=run_discord, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
