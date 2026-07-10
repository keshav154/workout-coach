"""
CoachxKeshav — Flask web UI + WhatsApp (Twilio) + Discord bot on Render.
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
        "name": "CoachxKeshav",
        "short_name": "CoachxKeshav",
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
const CACHE = 'coachxkeshav-v3';
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
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0a0a0f"/>
      <stop offset="1" stop-color="#12121a"/>
    </linearGradient>
    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="10" result="blur"/>
      <feMerge>
        <feMergeNode in="blur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
  </defs>
  <!-- chamfered square -->
  <polygon points="56,0 456,0 512,56 512,456 456,512 56,512 0,456 0,56"
    fill="url(#bg)" stroke="#00ff88" stroke-width="6"/>
  <!-- bolt, neon green with magenta/cyan offset for chromatic aberration -->
  <path d="M286 90 160 300h84l-30 132 138-228h-86z" fill="#ff00ff" opacity="0.55" transform="translate(-4,0)"/>
  <path d="M286 90 160 300h84l-30 132 138-228h-86z" fill="#00d4ff" opacity="0.55" transform="translate(4,0)"/>
  <path d="M286 90 160 300h84l-30 132 138-228h-86z" fill="#00ff88" filter="url(#glow)"/>
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
    from datetime import timedelta
    from agent_core import get_consecutive_workout_days, today as _today
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

    today      = _today()
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

    profile = load_profile() or {}
    return jsonify({
        "total_sessions":     len(sessions),
        "last_weight":        last_weight,
        "next_day":           day,
        "next_name":          p.get("name", ""),
        "sessions_this_week": sessions_this_week,
        "days_per_week":      profile.get("days_per_week", 6),
        "streak":             get_consecutive_workout_days(workout_log),
        "recent_sessions":    recent,
    })


@flask_app.route("/today_program")
@require_auth
def today_program():
    from agent_core import get_last_session_for_day, today_iso
    profile = load_profile()
    if not profile_complete(profile):
        return jsonify({"ready": False})
    workout_log = load_log()
    day  = get_next_day(workout_log)
    p    = PROGRAM.get(day, {})
    last = get_last_session_for_day(workout_log, day)

    # Rotation status: the most recent session by date (for the status line)
    sessions = workout_log.get("sessions", [])
    last_logged = None
    if sessions:
        idx = max(range(len(sessions)), key=lambda i: (sessions[i].get("date", ""), i))
        s = sessions[idx]
        last_logged = {"day": s.get("day"), "date": s.get("date"),
                       "name": PROGRAM.get(s.get("day"), {}).get("name", "")}
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
    return jsonify({"ready": True, "day": day, "name": p.get("name", ""),
                    "today": today_iso(), "last_logged": last_logged,
                    "exercises": exercises})


@flask_app.route("/chart_data")
@require_auth
def chart_data():
    """Series for the Progress tab charts: weight trend, weekly volume,
    and per-exercise top weight over time."""
    from datetime import datetime, timedelta
    from progression import session_volume

    mem      = load_memory()
    sessions = load_log().get("sessions", [])

    # Weight trend series from memory weight_log ("YYYY-MM-DD: XX.X kg")
    weight = []
    for e in mem.get("weight_log", []):
        try:
            d, w = e.split(": ")
            weight.append({"date": d, "kg": float(w.replace(" kg", ""))})
        except (ValueError, AttributeError):
            pass
    weight.sort(key=lambda x: x["date"])

    # Weekly volume (last 8 weeks, keyed by Monday)
    by_week = {}
    for s in sessions:
        try:
            d = datetime.strptime(s.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        wk = (d - timedelta(days=d.weekday())).isoformat()
        by_week[wk] = by_week.get(wk, 0) + session_volume(s)
    volume = [{"week": k, "kg": round(v)} for k, v in sorted(by_week.items())][-8:]

    # Per-exercise top weight per session date (exercises with 2+ data points)
    from agent_core import _num
    ex_hist: dict[str, list] = {}
    for s in sessions:
        d = s.get("date", "")
        for e in s.get("exercises", []):
            name = e.get("name")
            w = _num(e.get("weight"))
            if name and w > 0:
                ex_hist.setdefault(name, []).append({"date": d, "kg": w})
    exercises = {n: sorted(v, key=lambda x: x["date"])
                 for n, v in ex_hist.items() if len(v) >= 2}

    return jsonify({"weight": weight, "volume": volume, "exercises": exercises})


@flask_app.route("/log_workout", methods=["POST"])
@require_auth
def log_workout():
    from agent_core import (apply_memory_update, detect_prs, load_memory,
                            save_memory, save_session, today_iso)
    from trust import record_audit, validate_session

    data      = request.json or {}
    day       = data.get("day")
    raw       = [e for e in data.get("exercises", []) if e.get("weight") or e.get("reps_done") or e.get("sets")]
    if not day or not raw:
        return jsonify({"error": "Nothing to log — fill in at least one exercise."}), 400

    # Normalize: keep per-set detail, and derive a summary weight/reps_done
    # (the heaviest set) so PR detection and progression keep working.
    exercises = []
    for e in raw:
        sets = [s for s in (e.get("sets") or []) if s.get("weight") or s.get("reps")]
        item = {"name": e.get("name")}
        if sets:
            top = max(sets, key=lambda s: (float(s.get("weight") or 0), float(s.get("reps") or 0)))
            item["weight"]    = float(top.get("weight") or 0)
            item["reps_done"] = int(float(top.get("reps") or 0))
            item["sets"]      = sets
        else:
            item["weight"]    = float(e.get("weight") or 0)
            item["reps_done"] = int(float(e.get("reps_done") or 0))
        exercises.append(item)

    session = {"day": day, "date": today_iso(), "exercises": exercises}
    if data.get("body_weight_kg"):
        session["body_weight_kg"] = data["body_weight_kg"]

    ok, reason, cleaned = validate_session(session)
    if not ok:
        return jsonify({"error": reason}), 400

    workout_log = load_log()
    prs = detect_prs(workout_log, cleaned)
    save_session(workout_log, cleaned)
    record_audit("session", f"Day {day} (workout mode) on {cleaned['date']}",
                 ref={"date": cleaned["date"], "day": day})
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
    """Remove the most recent session BY DATE (not just the last array element)."""
    from agent_core import _col
    doc = _col("workout_log").find_one({"_id": "log"}) or {}
    sessions = doc.get("sessions", [])
    if not sessions:
        return jsonify({"ok": True, "modified": 0})
    # index of the latest-dated session (last among ties)
    target = max(range(len(sessions)), key=lambda i: (sessions[i].get("date", ""), i))
    removed = sessions.pop(target)
    _col("workout_log").update_one({"_id": "log"}, {"$set": {"sessions": sessions}})
    return jsonify({"ok": True, "modified": 1,
                    "removed": {"date": removed.get("date"), "day": removed.get("day")}})


@flask_app.route("/repair_data", methods=["POST"])
@require_auth
def repair_data():
    from agent_core import repair_workout_data
    result = repair_workout_data()
    return jsonify({"ok": True, **result})


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
    # Autonomous memory hygiene: merge duplicates, drop stale notes, distill
    # the week's episodes into durable observations.
    consolidated = None
    try:
        from memory_core import consolidate_memory
        consolidated = consolidate_memory()
        if consolidated:
            log.info(consolidated)
    except Exception as e:
        log.error(f"Memory consolidation failed: {e}")
    log.info(f"Weekly cron: report={'sent' if sent else 'failed'}")
    return jsonify({"sent": sent, "message": msg, "memory": consolidated})


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
        sent = send_telegram_document(dump, fname, caption="CoachxKeshav weekly backup")
        log.info(f"Backup cron: {'sent' if sent else 'failed'} ({len(dump)} bytes)")
        return jsonify({"sent": sent, "bytes": len(dump)})
    except Exception as e:
        log.error(f"Backup cron failed: {e}", exc_info=True)
        alert_admin(f"Backup failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@flask_app.route("/cron/selfheal", methods=["GET", "POST"])
def cron_selfheal():
    """Autonomous self-healing: repair workout data on a schedule; only
    notify if something was actually wrong and got fixed."""
    if not _cron_authorized():
        return "forbidden", 403
    record_event("cron_selfheal")
    from agent_core import repair_workout_data
    try:
        result = repair_workout_data()
        sent = False
        if result["removed_duplicates"] or result["fixed_dates"]:
            msg = (f"🧹 Self-check: fixed {result['fixed_dates']} bad date(s) and removed "
                   f"{result['removed_duplicates']} duplicate session(s) automatically. "
                   f"Everything's back in order.")
            sent = notify(msg)
        log.info(f"Self-heal cron: {result}")
        return jsonify({"ok": True, "notified": sent, **result})
    except Exception as e:
        log.error(f"Self-heal cron failed: {e}", exc_info=True)
        alert_admin(f"Self-heal failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@flask_app.route("/cron/plateau", methods=["GET", "POST"])
def cron_plateau():
    """Autonomous plateau intervention: detect stalled lifts and schedule an
    automatic deload for their next occurrence — no need to ask permission."""
    if not _cron_authorized():
        return "forbidden", 403
    record_event("cron_plateau")
    from agent_core import load_log
    from progression import detect_plateau_exercise_names, set_autodeload_flags
    try:
        names = detect_plateau_exercise_names(load_log())
        newly = set_autodeload_flags(names)
        sent = False
        if newly:
            msg = ("📉 I noticed these lifts have plateaued: " + ", ".join(newly) +
                   ".\nI've scheduled a 10% deload for each next time they come up — "
                   "no action needed, I'll handle it in your next relevant session.")
            sent = notify(msg)
        log.info(f"Plateau cron: flagged {newly}")
        return jsonify({"ok": True, "newly_flagged": newly, "notified": sent})
    except Exception as e:
        log.error(f"Plateau cron failed: {e}", exc_info=True)
        alert_admin(f"Plateau cron failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@flask_app.route("/cron/evening", methods=["GET", "POST"])
def cron_evening():
    """Autonomous evening loop: nudge for nutrition/weight if missing, and
    write today's episodic memory summary so tomorrow's agent remembers today."""
    if not _cron_authorized():
        return "forbidden", 403
    record_event("cron_evening")
    from reports import build_evening_checkin
    from memory_core import summarize_today
    try:
        msg = build_evening_checkin()
        sent = notify(msg) if msg else False
        episode = None
        try:
            episode = summarize_today()
        except Exception as e:
            log.error(f"Episode summary failed: {e}")
        log.info(f"Evening cron: nudge={'sent' if sent else 'skipped'}, "
                 f"episode={'saved' if episode else 'none'}")
        return jsonify({"sent": sent, "message": msg, "episode": episode})
    except Exception as e:
        log.error(f"Evening cron failed: {e}", exc_info=True)
        alert_admin(f"Evening cron failed: {e}")
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
