"""
CoachxKeshav — Flask web UI + WhatsApp (Twilio) + Discord bot on Render.
All message routing lives in messaging.process_message; this file is just transport.
Data persisted in MongoDB Atlas.
"""

import functools
import hmac
import logging
import os
import threading
import time
from collections import deque

import discord
from flask import Flask, jsonify, render_template, request

from agent_core import (
    get_next_day,
    get_program,
    get_rotation,
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
from monitor import (alert_admin, clear_notify_failure, get_status, job_done,
                     mark_job_done, record_event, record_notify_failure)
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

if not CRON_SECRET:
    log.warning("CRON_SECRET not set — /cron/* endpoints are open to anyone. "
                "Set it (and add ?secret=... to your cron pings) to lock them down.")


# Brute-force guard: after 10 failed password attempts from one IP within
# 5 minutes, reject further attempts from it until the window clears.
_AUTH_FAILS: dict[str, list] = {}


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "?")


def require_auth(f):
    """Check X-Password header sent by the JS frontend on every API call."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not WEB_PASSWORD:
            return f(*args, **kwargs)
        if request.path == "/" or (request.method == "GET" and request.path in ("/health",)):
            return f(*args, **kwargs)
        ip  = _client_ip()
        now = time.time()
        fails = [t for t in _AUTH_FAILS.get(ip, []) if now - t < 300]
        if len(fails) >= 10:
            _AUTH_FAILS[ip] = fails
            return jsonify({"error": "too many attempts — wait a few minutes"}), 429
        pwd = request.headers.get("X-Password", "")
        if hmac.compare_digest(pwd, WEB_PASSWORD):
            _AUTH_FAILS.pop(ip, None)
            return f(*args, **kwargs)
        fails.append(now)
        _AUTH_FAILS[ip] = fails
        return jsonify({"error": "unauthorized"}), 401
    return decorated


# ── Flask web app ──────────────────────────────────────────────────────────────
flask_app = Flask(__name__)
flask_app.secret_key = FLASK_SECRET
flask_app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024   # voice notes are the largest upload


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
const CACHE = 'coachxkeshav-v15';
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
    p   = get_program().get(day, {})
    return jsonify({"day": day, "name": p.get("name", ""), "focus": p.get("focus", "")})


@flask_app.route("/dashboard")
@require_auth
def dashboard():
    """One-shot 'Today' home screen payload — aggregates everything so the
    landing view loads in a single request (matters on a cold free dyno)."""
    from datetime import timedelta
    from agent_core import (compute_targets, effective_calorie_target,
                            get_consecutive_workout_days, get_consistent_weeks,
                            get_weight_entries, is_rest_day, today as _today,
                            today_iso)
    from nutrition import today_totals
    from water import water_goal_ml, water_today

    profile = load_profile()
    if not profile_complete(profile):
        return jsonify({"ready": False})

    workout_log = load_log()
    sessions = workout_log.get("sessions", [])
    day = get_next_day(workout_log)
    p = get_program().get(day, {})
    now = _today()
    worked_out = any(s.get("date") == today_iso() for s in sessions)
    week_start = now - timedelta(days=now.weekday())
    dpw = profile.get("days_per_week", 6)

    targets = compute_targets(profile)
    nt = today_totals()
    wt = water_today()
    entries = get_weight_entries(load_memory())

    habits = _habit_rows()

    return jsonify({
        "ready":       True,
        "name":        profile.get("name", ""),
        "date":        today_iso(),
        "workout":     {"day": day, "name": p.get("name", ""), "focus": p.get("focus", ""),
                        "exercises": len(p.get("exercises", [])),
                        "done_today": worked_out, "rest_day": is_rest_day()},
        "water":       {"ml": wt["ml"], "goal": water_goal_ml(profile)},
        "nutrition":   {"calories": nt["calories"], "protein_g": nt["protein_g"],
                        "cal_target": effective_calorie_target(profile, targets),
                        "protein_target": targets["protein_target_g"],
                        "meals": nt["count"]},
        "habits":      habits,
        "streak":      get_consecutive_workout_days(workout_log),
        "consistent_weeks": get_consistent_weeks(workout_log, dpw),
        "sessions_this_week": sum(1 for s in sessions if s.get("date", "") >= week_start.isoformat()),
        "days_per_week": dpw,
        "last_weight": entries[-1][1] if entries else None,
    })


@flask_app.route("/stats")
@require_auth
def stats():
    from datetime import timedelta
    from agent_core import get_consecutive_workout_days, today as _today
    workout_log = load_log()
    mem         = load_memory()
    sessions    = workout_log.get("sessions", [])
    day         = get_next_day(workout_log)
    prog        = get_program()
    p           = prog.get(day, {})

    from agent_core import get_weight_entries
    entries = get_weight_entries(mem)
    last_weight = entries[-1][1] if entries else None

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
            "name":      prog.get(d, {}).get("name", ""),
            "date":      s.get("date", ""),
            "weight":    s.get("body_weight_kg"),
            "duration":  s.get("duration_min"),
            "exercises": len(s.get("exercises", [])),
            "detail":    [{"name":   e.get("name"),
                           "weight": e.get("weight"),
                           "reps":   e.get("reps_done"),
                           "note":   e.get("note"),
                           "sets":   e.get("sets") if isinstance(e.get("sets"), list) else None}
                          for e in s.get("exercises", [])],
        })

    from agent_core import get_consistent_weeks
    from progression import detect_plateaus, weekly_volume
    profile = load_profile() or {}
    dpw = profile.get("days_per_week", 6)
    return jsonify({
        "total_sessions":     len(sessions),
        "last_weight":        last_weight,
        "next_day":           day,
        "next_name":          p.get("name", ""),
        "sessions_this_week": sessions_this_week,
        "days_per_week":      dpw,
        "streak":             get_consecutive_workout_days(workout_log),
        "consistent_weeks":   get_consistent_weeks(workout_log, dpw),
        "recent_sessions":    recent,
        "plateaus":           detect_plateaus(workout_log),
        "volume":             weekly_volume(workout_log),
    })


@flask_app.route("/today_program")
@require_auth
def today_program():
    from agent_core import (get_last_session_for_day, should_suggest_deload,
                            today_iso, warmup_weight_for)
    from progression import alternatives_for, get_autodeload_flags, suggest_next
    profile = load_profile()
    if not profile_complete(profile):
        return jsonify({"ready": False})
    workout_log = load_log()
    prog = get_program()
    rotation = get_rotation()
    day  = get_next_day(workout_log)
    p    = prog.get(day, {})
    last = get_last_session_for_day(workout_log, day)
    deload_week = should_suggest_deload(workout_log)
    autoflags   = set(get_autodeload_flags())

    # Rotation status: the most recent session by date (for the status line)
    sessions = workout_log.get("sessions", [])
    last_logged = None
    if sessions:
        idx = max(range(len(sessions)), key=lambda i: (sessions[i].get("date", ""), i))
        s = sessions[idx]
        last_logged = {"day": s.get("day"), "date": s.get("date"),
                       "name": prog.get(s.get("day"), {}).get("name", "")}
    exercises = []
    for ex in p.get("exercises", []):
        prev = None
        if last:
            prev = next((e for e in last.get("exercises", []) if e["name"] == ex["name"]), None)
        hist = []
        for s in reversed(sessions):
            e = next((x for x in s.get("exercises", []) if x.get("name") == ex["name"]), None)
            if e:
                hist.append({"date": s.get("date"), "weight": e.get("weight"),
                             "reps": e.get("reps_done")})
            if len(hist) >= 5:
                break
        # Deload week = ~60% of normal (banner says so); a per-exercise plateau
        # flag is a lighter ~10% touch. Week takes precedence when both apply.
        deload_factor = 0.6 if deload_week else (0.9 if ex["name"] in autoflags else None)
        suggestion = suggest_next(ex["rep_range"],
                                  prev.get("weight") if prev else None,
                                  prev.get("reps_done") if prev else None,
                                  deload_factor=deload_factor)
        exercises.append({
            "name":          ex["name"],
            "sets":          ex["sets"],
            "rep_range":     ex["rep_range"],
            "scheme":        ex.get("scheme"),
            "last_weight":   prev.get("weight") if prev else None,
            "last_reps":     prev.get("reps_done") if prev else None,
            "warmup_weight": warmup_weight_for((suggestion or {}).get("weight")
                                               if suggestion else (prev.get("weight") if prev else None)),
            "alternatives":  alternatives_for(ex["name"]),
            "suggestion":    suggestion,
            "history":       hist,
        })
    # Week-ahead preview: every rotation day with its exercises + last weights,
    # so tapping a rotation chip can show what's coming.
    week = []
    for d0 in rotation:
        pd = prog.get(d0, {})
        lastd = get_last_session_for_day(workout_log, d0)
        exs = []
        for ex in pd.get("exercises", []):
            prev0 = next((e for e in (lastd or {}).get("exercises", [])
                          if e.get("name") == ex["name"]), None)
            exs.append({"name": ex["name"],
                        "target": ex.get("scheme") or f"{ex['sets']}x{ex['rep_range']}",
                        "last_weight": prev0.get("weight") if prev0 else None})
        week.append({"day": d0, "name": pd.get("name", ""), "focus": pd.get("focus", ""),
                     "exercises": exs})

    from agent_core import get_consecutive_workout_days, is_rest_day
    consec = get_consecutive_workout_days(workout_log)
    return jsonify({"ready": True, "day": day, "name": p.get("name", ""),
                    "today": today_iso(), "last_logged": last_logged,
                    "warmup": p.get("warmup", ""),
                    "rotation": [{"day": d0, "name": prog.get(d0, {}).get("name", "")}
                                 for d0 in rotation],
                    "week": week,
                    "deload": should_suggest_deload(workout_log),
                    "autodeload": get_autodeload_flags(),
                    "rest_day": is_rest_day(),
                    "rest_suggested": consec >= max(3, len(rotation)),
                    "consecutive_days": consec,
                    "exercises": exercises})


@flask_app.route("/rest_day", methods=["POST"])
@require_auth
def rest_day_route():
    """Toggle today as a rest day from the Workout tab."""
    from agent_core import is_rest_day, mark_rest_day, unmark_rest_day
    if is_rest_day():
        unmark_rest_day()
        return jsonify({"ok": True, "rest_day": False})
    mark_rest_day()
    return jsonify({"ok": True, "rest_day": True})


@flask_app.route("/program", methods=["GET", "POST"])
@require_auth
def program_route():
    """Get or save the training program (custom or default) for the builder."""
    from agent_core import (get_program, program_is_custom, reset_program,
                            save_program)
    if request.method == "POST":
        data = request.json or {}
        if data.get("reset"):
            reset_program()
            return jsonify({"ok": True, "reset": True})
        ok, msg = save_program(data.get("days") or [])
        if not ok:
            return jsonify({"error": msg}), 400
        return jsonify({"ok": True})
    prog = get_program()
    days = [{"id": did, "name": d.get("name", ""), "focus": d.get("focus", ""),
             "warmup": d.get("warmup", ""),
             "exercises": [{"name": e.get("name"),
                            "sets": e.get("sets", 3),
                            "rep_range": e.get("rep_range", "8-12")}
                           for e in d.get("exercises", [])]}
            for did, d in prog.items()]
    return jsonify({"days": days, "is_custom": program_is_custom()})


@flask_app.route("/exercise_library")
@require_auth
def exercise_library_route():
    from agent_core import exercise_library
    return jsonify({"exercises": exercise_library()})


@flask_app.route("/chart_data")
@require_auth
def chart_data():
    """Series for the Progress tab charts: weight trend, weekly volume,
    and per-exercise top weight over time."""
    from datetime import datetime, timedelta
    from progression import session_volume

    from agent_core import get_weight_entries
    mem      = load_memory()
    sessions = load_log().get("sessions", [])

    weight = [{"date": d, "kg": w} for d, w in get_weight_entries(mem)]

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

    # Per-exercise top weight + estimated 1RM (Epley) per session date
    from agent_core import _num
    ex_hist: dict[str, list] = {}
    for s in sessions:
        d = s.get("date", "")
        for e in s.get("exercises", []):
            name = e.get("name")
            w = _num(e.get("weight"))
            r = _num(e.get("reps_done"))
            if name and w > 0:
                point = {"date": d, "kg": w}
                if r > 0:
                    point["e1rm"] = round(w * (1 + r / 30), 1)
                ex_hist.setdefault(name, []).append(point)
    exercises = {n: sorted(v, key=lambda x: x["date"])
                 for n, v in ex_hist.items() if len(v) >= 2}

    return jsonify({"weight": weight, "volume": volume, "exercises": exercises})


def _save_progress_photo(b64: str, mime: str = "image/jpeg") -> None:
    """Store a progress photo (base64) and trim the collection to the last 60."""
    from agent_core import _col, today_iso
    col = _col("photos")
    col.insert_one({"date": today_iso(), "mime": mime, "b64": b64})
    docs = list(col.find())
    if len(docs) > 60:
        for d in sorted(docs, key=lambda x: x.get("date", ""))[:len(docs) - 60]:
            col.delete_one({"_id": d["_id"]})


@flask_app.route("/photos", methods=["GET", "POST"])
@require_auth
def photos():
    from agent_core import _col
    if request.method == "POST":
        import base64
        data = request.json or {}
        b64  = str(data.get("b64", ""))
        mime = data.get("mime", "image/jpeg")
        if not b64:
            return jsonify({"error": "no image"}), 400
        if len(b64) > 1_200_000:      # ~900 KB binary
            return jsonify({"error": "image too large — try again"}), 400
        if not mime.startswith("image/"):
            return jsonify({"error": "not an image"}), 400
        try:
            if not base64.b64decode(b64, validate=True):
                raise ValueError
        except Exception:
            return jsonify({"error": "corrupt image data"}), 400
        _save_progress_photo(b64, mime)
        return jsonify({"ok": True})
    metas = [{"id": str(d.get("_id")), "date": d.get("date", "")}
             for d in sorted(_col("photos").find(), key=lambda x: x.get("date", ""))]
    return jsonify({"photos": metas})


@flask_app.route("/photo/<pid>")
@require_auth
def photo(pid):
    import base64
    from bson import ObjectId
    from agent_core import _col
    doc = None
    try:
        doc = _col("photos").find_one({"_id": ObjectId(pid)})
    except Exception:
        pass
    if doc is None:
        doc = _col("photos").find_one({"_id": pid})
    if not doc:
        return "not found", 404
    try:
        raw = base64.b64decode(doc.get("b64", ""))
    except Exception:
        return "corrupt image", 500
    return raw, 200, {"Content-Type": doc.get("mime", "image/jpeg"),
                      "Cache-Control": "private, max-age=86400"}


# ── Habits ─────────────────────────────────────────────────────────────────────
def _habit_rows() -> list[dict]:
    from datetime import datetime as _dt, timedelta as _td
    from agent_core import _col, today
    names = [d["_id"] for d in _col("habits").find()]
    logs: dict[str, set] = {}
    for l in _col("habit_log").find():
        if l.get("name") and l.get("date"):
            try:
                logs.setdefault(l["name"], set()).add(
                    _dt.strptime(l["date"], "%Y-%m-%d").date())
            except ValueError:
                pass
    now = today()
    out = []
    for name in sorted(names):
        dates = logs.get(name, set())
        anchor = now if now in dates else (now - _td(days=1))
        streak, d = 0, anchor
        while d in dates:
            streak += 1
            d -= _td(days=1)
        out.append({"name": name, "done_today": now in dates, "streak": streak})
    return out


@flask_app.route("/habits")
@require_auth
def habits():
    return jsonify({"habits": _habit_rows()})


@flask_app.route("/habit_toggle", methods=["POST"])
@require_auth
def habit_toggle():
    from agent_core import _col, today_iso
    name = ((request.json or {}).get("name") or "").strip()
    if not name or not _col("habits").find_one({"_id": name}):
        return jsonify({"error": "unknown habit"}), 400
    existing = [l for l in _col("habit_log").find()
                if l.get("name") == name and l.get("date") == today_iso()]
    if existing:
        for l in existing:
            _col("habit_log").delete_one({"_id": l["_id"]})
    else:
        _col("habit_log").insert_one({"date": today_iso(), "name": name})
    return jsonify({"ok": True, "habits": _habit_rows()})


@flask_app.route("/measurements")
@require_auth
def measurements():
    """Body measurement series per part, for the Progress tab chart."""
    from agent_core import _col
    series: dict[str, list] = {}
    for d in sorted(_col("measurements").find(), key=lambda x: x.get("date", "")):
        part, cm = d.get("part"), d.get("cm")
        if part and cm:
            series.setdefault(part, []).append({"date": d.get("date", ""), "cm": float(cm)})
    latest = {p: v[-1] for p, v in series.items() if v}
    return jsonify({"series": series, "latest": latest})


@flask_app.route("/goals_data")
@require_auth
def goals_data():
    """Goal progress bars: current vs target with % progress and projection note."""
    from goals import _best_lift, _project_lift, _project_weight, _weight_series, get_goals
    out = []
    for g in get_goals():
        target = float(g.get("target") or 0)
        if g.get("kind") == "weight":
            series = [x for x in _weight_series()
                      if not g.get("created") or x[0].isoformat() >= g["created"]] or _weight_series()
            if not series or not target:
                continue
            start, current = series[0][1], series[-1][1]
            total = abs(target - start)
            pct = 100 if total < 1e-9 else max(0, min(100, (1 - abs(target - current) / total) * 100))
            out.append({"kind": "weight", "label": f"Body weight → {target:g} kg",
                        "current": current, "target": target, "unit": "kg",
                        "pct": round(pct), "note": _project_weight(g),
                        "by_date": g.get("by_date")})
        else:
            if not (g.get("exercise") or "").strip() or not target:
                continue        # malformed legacy goal — nothing meaningful to show
            best = _best_lift(g["exercise"])
            current = best[1] if best else 0
            pct = max(0, min(100, current / target * 100))
            out.append({"kind": "lift", "label": f"{g.get('exercise', '?')} → {target:g} kg",
                        "current": current, "target": target, "unit": "kg",
                        "pct": round(pct), "note": _project_lift(g), "by_date": g.get("by_date")})
    return jsonify({"goals": out})


@flask_app.route("/achievements")
@require_auth
def achievements():
    """Milestone badges computed from real data."""
    from progression import session_volume
    sessions = load_log().get("sessions", [])
    mem = load_memory()

    from datetime import datetime as _dt
    dates = set()
    for s in sessions:
        try:
            dates.add(_dt.strptime(s.get("date", ""), "%Y-%m-%d").date())
        except (ValueError, TypeError):
            pass
    best_streak, run = 0, 0
    prev = None
    for d in sorted(dates):
        run = run + 1 if (prev and (d - prev).days == 1) else 1
        best_streak = max(best_streak, run)
        prev = d

    total_volume = round(sum(session_volume(s) for s in sessions))
    metrics = [
        ("🏋️", "sessions",   len(sessions),                      [10, 25, 50, 100, 200]),
        ("🔥", "day streak", best_streak,                         [3, 6, 12, 24]),
        ("🏆", "PRs",        len(mem.get("personal_records", [])), [5, 15, 30, 60]),
        ("⚖️", "weigh-ins",  len(mem.get("weight_log", [])),      [4, 12, 26, 52]),
        ("🌋", "kg lifted",  total_volume,                        [10_000, 50_000, 150_000, 500_000]),
    ]
    badges = []
    for icon, label, value, thresholds in metrics:
        for t in thresholds:
            badges.append({"icon": icon, "label": f"{t:,} {label}",
                           "achieved": value >= t, "value": value, "target": t})
    return jsonify({"badges": badges})


@flask_app.route("/export_csv")
@require_auth
def export_csv():
    """Download workouts / expenses / meals / weight as CSV."""
    import csv
    import io
    from agent_core import _col
    what = request.args.get("what", "workouts")
    buf = io.StringIO()
    w = csv.writer(buf)
    if what == "workouts":
        w.writerow(["date", "day", "exercise", "top_weight_kg", "top_reps",
                    "sets", "body_weight_kg", "duration_min"])
        for s in load_log().get("sessions", []):
            for e in s.get("exercises", []):
                sets = ";".join(f"{x.get('weight')}x{x.get('reps')}"
                                for x in (e.get("sets") or []) if isinstance(x, dict))
                w.writerow([s.get("date"), s.get("day"), e.get("name"), e.get("weight"),
                            e.get("reps_done"), sets, s.get("body_weight_kg"),
                            s.get("duration_min")])
    elif what == "expenses":
        w.writerow(["date", "amount", "category", "description", "note"])
        for d in sorted(_col("expenses").find(), key=lambda x: x.get("date", "")):
            w.writerow([d.get("date"), d.get("amount"), d.get("category"),
                        d.get("description"), d.get("note")])
    elif what == "meals":
        w.writerow(["date", "description", "calories", "protein_g"])
        for d in sorted(_col("meals").find(), key=lambda x: x.get("date", "")):
            w.writerow([d.get("date"), d.get("description"), d.get("calories"),
                        d.get("protein_g")])
    elif what == "weight":
        from agent_core import get_weight_entries
        w.writerow(["date", "kg"])
        for d, kg in get_weight_entries(load_memory()):
            w.writerow([d, kg])
    else:
        return jsonify({"error": "unknown export"}), 400
    return buf.getvalue(), 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": f"attachment; filename=coachx_{what}.csv",
    }


@flask_app.route("/weekly_summary")
@require_auth
def weekly_summary():
    """Deterministic weekly recap data for the printable summary view."""
    from reports import weekly_summary_data
    try:
        offset = max(0, min(52, int(request.args.get("offset", "0"))))
    except (TypeError, ValueError):
        offset = 0
    return jsonify(weekly_summary_data(offset))


@flask_app.route("/records")
@require_auth
def records():
    """Record wall: best weight x reps per exercise, plus recent PR feed."""
    from agent_core import _num
    best: dict[str, tuple] = {}
    for s in load_log().get("sessions", []):
        d = s.get("date", "")
        for e in s.get("exercises", []):
            name = e.get("name")
            w, r = _num(e.get("weight")), _num(e.get("reps_done"))
            if not name or w <= 0:
                continue
            if name not in best or (w, r) > best[name][:2]:
                best[name] = (w, r, d)
    mem = load_memory()
    return jsonify({
        "best": [{"name": n, "weight": w, "reps": r, "date": d}
                 for n, (w, r, d) in sorted(best.items(), key=lambda x: -x[1][0])],
        "recent_prs": mem.get("personal_records", [])[-10:],
    })


@flask_app.route("/nutrition_data")
@require_auth
def nutrition_data():
    """Fuel tab: today's meals, totals vs targets, and the last 7 days."""
    from agent_core import compute_targets, effective_calorie_target
    from nutrition import get_meals, today_totals, week_series
    profile = load_profile() or {}
    if profile:
        targets = compute_targets(profile)
        cal_t, prot_t = effective_calorie_target(profile, targets), targets["protein_target_g"]
    else:
        cal_t = prot_t = 0
    totals = today_totals()
    gap = prot_t - totals["protein_g"]
    protein_fix = None
    if prot_t and gap >= 12:            # meaningfully short on protein today
        protein_fix = {"gap": round(gap), "options": _protein_options(gap)}
    return jsonify({
        "meals":       get_meals(),
        "totals":      totals,
        "targets":     {"calories": cal_t, "protein_g": prot_t},
        "week":        week_series(),
        "protein_fix": protein_fix,
    })


# Indian veg high-protein options (name, kcal, protein g) for closing a gap.
_PROTEIN_FOODS = [
    ("Paneer bhurji (150g)", 400, 30),
    ("Soya chunks sabzi (50g dry)", 180, 26),
    ("Protein shake (1 scoop)", 120, 24),
    ("Rajma chawal", 450, 16),
    ("Tofu bhurji (150g)", 180, 16),
    ("Moong dal chilla (2 pcs)", 250, 14),
    ("Curd + sprouts", 200, 14),
    ("Greek curd (200g)", 130, 18),
]


def _protein_options(gap: float) -> list[dict]:
    """1-2 suggestions that roughly close the protein gap without overshooting
    calories more than needed."""
    picks = []
    # Prefer a single item that covers most of the gap; else pair two.
    single = min((f for f in _PROTEIN_FOODS if f[2] >= gap * 0.8),
                 key=lambda f: f[1], default=None)
    if single:
        picks = [single]
    else:
        ordered = sorted(_PROTEIN_FOODS, key=lambda f: -f[2])
        total, chosen = 0, []
        for f in ordered:
            chosen.append(f); total += f[2]
            if total >= gap or len(chosen) >= 2:
                break
        picks = chosen[:2]
    return [{"name": n, "calories": c, "protein_g": p} for n, c, p in picks]


@flask_app.route("/water")
@require_auth
def water():
    from water import GLASS_ML, water_goal_ml, water_today, water_week
    profile = load_profile() or {}
    t = water_today()
    return jsonify({
        "ml":       t["ml"],
        "count":    t["count"],
        "goal":     water_goal_ml(profile),
        "glass_ml": GLASS_ML,
        "week":     water_week(),
    })


@flask_app.route("/water_add", methods=["POST"])
@require_auth
def water_add():
    from water import add_water, water_goal_ml, water_today
    try:
        ml = int(float((request.json or {}).get("ml", 250)))
    except (TypeError, ValueError):
        return jsonify({"error": "bad amount"}), 400
    if not (1 <= ml <= 3000):
        return jsonify({"error": "amount out of range"}), 400
    add_water(ml)
    t = water_today()
    return jsonify({"ok": True, "ml": t["ml"], "count": t["count"], "goal": water_goal_ml()})


@flask_app.route("/water_undo", methods=["POST"])
@require_auth
def water_undo():
    from water import undo_water, water_today
    undo_water()
    t = water_today()
    return jsonify({"ok": True, "ml": t["ml"], "count": t["count"]})


@flask_app.route("/money_data")
@require_auth
def money_data():
    """Money tab: month total, category breakdown vs budgets, recent transactions."""
    from agent_core import today as _today
    from expense_core import get_budget, get_expenses
    month    = _today().strftime("%Y-%m")
    expenses = get_expenses(month)
    budget   = get_budget()
    totals, daily = {}, {}
    for e in expenses:
        amt = e.get("amount", 0) or 0
        totals[e.get("category", "Other")] = totals.get(e.get("category", "Other"), 0) + amt
        daily[e.get("date", "")] = daily.get(e.get("date", ""), 0) + amt
    total = sum(totals.values())
    by_category = [{"category": c, "amount": v, "budget": budget.get(c)}
                   for c, v in sorted(totals.items(), key=lambda x: -x[1])]
    recent = sorted(expenses, key=lambda x: (x.get("date", ""), x.get("id", "")), reverse=True)[:20]
    return jsonify({
        "month":       month,
        "total":       total,
        "avg_per_day": total / len(daily) if daily else 0,
        "by_category": by_category,
        "recent":      recent,
    })


@flask_app.route("/log_expense", methods=["POST"])
@require_auth
def log_expense_route():
    """Inline expense logging from the Money tab — no chat round-trip."""
    from expense_core import CATEGORIES, log_expense
    from trust import record_audit, validate_expense
    data = request.json or {}
    ok, reason = validate_expense(data.get("amount"))
    if not ok:
        return jsonify({"error": reason}), 400
    amount = float(data["amount"])
    desc   = str(data.get("description") or "").strip()[:80] or "expense"
    cat    = str(data.get("category") or "Other").capitalize()
    if cat not in CATEGORIES:
        cat = "Other"
    entry = log_expense(amount=amount, description=desc, category=cat)
    record_audit("expense", f"Rs {entry['amount']:,.0f} {cat} — {desc}", ref=entry.get("id"))
    return jsonify({"ok": True})


@flask_app.route("/log_meal", methods=["POST"])
@require_auth
def log_meal_route():
    """Inline meal logging from the Fuel tab (quick-repeat chips)."""
    from nutrition import log_meal, today_totals
    from trust import record_audit
    data = request.json or {}
    desc = str(data.get("description") or "").strip()[:120]
    if not desc:
        return jsonify({"error": "describe the meal"}), 400
    try:
        cal  = float(data.get("calories") or 0)
        prot = float(data.get("protein_g") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "calories/protein must be numbers"}), 400
    if cal < 0 or prot < 0 or cal > 12000:
        return jsonify({"error": "those numbers look wrong"}), 400
    entry = log_meal(desc, cal, prot)
    record_audit("meal", f"{desc} ({cal:g} kcal, {prot:g}g)", ref=entry.get("id"))
    return jsonify({"ok": True, "totals": today_totals()})


@flask_app.route("/meal_quick")
@require_auth
def meal_quick():
    """One-tap meal suggestions: most frequent meals (last 90 days) and
    yesterday's meals for a 'repeat yesterday' action."""
    from collections import Counter
    from datetime import timedelta
    from agent_core import _col, today
    cutoff = (today() - timedelta(days=90)).isoformat()
    yday   = (today() - timedelta(days=1)).isoformat()
    counts: Counter = Counter()
    info: dict[str, dict] = {}
    yesterday = []
    for m in _col("meals").find():
        d, desc = m.get("date", ""), (m.get("description") or "").strip()
        if not desc or d < cutoff:
            continue
        key = desc.lower()
        counts[key] += 1
        info[key] = {"description": desc,
                     "calories": float(m.get("calories") or 0),
                     "protein_g": float(m.get("protein_g") or 0)}
        if d == yday:
            yesterday.append({"description": desc,
                              "calories": float(m.get("calories") or 0),
                              "protein_g": float(m.get("protein_g") or 0)})
    frequent = [dict(info[k], count=c) for k, c in counts.most_common(8) if c >= 2]
    return jsonify({"frequent": frequent, "yesterday": yesterday})


@flask_app.route("/voice", methods=["POST"])
@require_auth
def voice_route():
    """Web voice input: audio blob in, transcription + coach reply out."""
    f = request.files.get("audio")
    if f is None:
        return jsonify({"error": "no audio uploaded"}), 400
    data = f.read()
    if not data:
        return jsonify({"error": "empty audio"}), 400
    if len(data) > 20 * 1024 * 1024:
        return jsonify({"error": "audio too large"}), 400
    try:
        reply = transcribe_and_process(data, source="web", filename=f.filename or "voice.webm")
    except Exception as e:
        log.error(f"Voice error: {e}", exc_info=True)
        return jsonify({"error": "Couldn't process that voice note, please try again"}), 500
    return jsonify({"reply": reply})


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
        note = str(e.get("note") or "").strip()[:200]
        if note:
            item["note"] = note
        exercises.append(item)

    session = {"day": day, "date": today_iso(), "exercises": exercises}
    if data.get("body_weight_kg"):
        session["body_weight_kg"] = data["body_weight_kg"]
    if data.get("duration_min"):
        try:
            dur = int(float(data["duration_min"]))
            if 1 <= dur <= 600:
                session["duration_min"] = dur
        except (TypeError, ValueError):
            pass

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
    reset_history("telegram")
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


@flask_app.route("/update_session", methods=["POST"])
@require_auth
def update_session():
    """Edit a past session identified by date+day. Overwrites its exercises
    (top set weight/reps), body weight and duration. Per-set detail is kept
    only for exercises whose top set is unchanged."""
    from agent_core import _col, _num
    from trust import record_audit, validate_session
    data = request.json or {}
    date, day = data.get("date"), data.get("day")
    if not date or not day:
        return jsonify({"error": "missing session id"}), 400
    doc = _col("workout_log").find_one({"_id": "log"}) or {}
    sessions = doc.get("sessions", [])
    idx = next((i for i, s in enumerate(sessions)
                if s.get("date") == date and s.get("day") == day), None)
    if idx is None:
        return jsonify({"error": "session not found"}), 404

    old_ex = {e.get("name"): e for e in sessions[idx].get("exercises", [])}
    new_ex = []
    for e in data.get("exercises", []):
        name = (e.get("name") or "").strip()
        if not name:
            continue
        w = _num(e.get("weight"))
        r = int(_num(e.get("reps")))
        if w <= 0 and r <= 0:
            continue                       # blanked out — drop this exercise
        item = {"name": name, "weight": w, "reps_done": r}
        prev = old_ex.get(name)
        # Preserve per-set detail only if the top set is unchanged.
        if prev and isinstance(prev.get("sets"), list) and \
           _num(prev.get("weight")) == w and int(_num(prev.get("reps_done"))) == r:
            item["sets"] = prev["sets"]
        if prev and prev.get("note"):
            item["note"] = prev["note"]
        new_ex.append(item)

    if not new_ex:
        return jsonify({"error": "a session needs at least one exercise — "
                                 "delete it instead if you meant to remove it"}), 400

    session = {"day": day, "date": date, "exercises": new_ex}
    if data.get("body_weight_kg"):
        try:
            session["body_weight_kg"] = float(data["body_weight_kg"])
        except (TypeError, ValueError):
            pass
    if data.get("duration_min"):
        try:
            dur = int(float(data["duration_min"]))
            if 1 <= dur <= 600:
                session["duration_min"] = dur
        except (TypeError, ValueError):
            pass

    ok, reason, cleaned = validate_session(session)
    if not ok:
        return jsonify({"error": reason}), 400
    cleaned["date"], cleaned["day"] = date, day     # validate_session may reset date
    sessions[idx] = cleaned
    _col("workout_log").update_one({"_id": "log"}, {"$set": {"sessions": sessions}})
    record_audit("edit", f"Edited Day {day} on {date}")
    return jsonify({"ok": True})


@flask_app.route("/delete_session", methods=["POST"])
@require_auth
def delete_session():
    """Delete a specific session by date+day (from the Progress tab)."""
    from agent_core import _col
    data = request.json or {}
    date, day = data.get("date"), data.get("day")
    if not date or not day:
        return jsonify({"error": "missing session id"}), 400
    _col("workout_log").update_one(
        {"_id": "log"}, {"$pull": {"sessions": {"date": date, "day": day}}})
    return jsonify({"ok": True})


@flask_app.route("/clear_autodeload", methods=["POST"])
@require_auth
def clear_autodeload():
    """Cancel a scheduled auto-deload flag for one exercise."""
    from progression import clear_autodeload_flag
    name = ((request.json or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "missing name"}), 400
    clear_autodeload_flag(name)
    return jsonify({"ok": True})


@flask_app.route("/backup_download")
@require_auth
def backup_download():
    """Download a full JSON backup on demand — a Telegram-independent copy."""
    from agent_core import today_iso
    dump = export_all()
    return dump, 200, {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Disposition": f"attachment; filename=coachx_backup_{today_iso()}.json",
    }


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


def _cron_force() -> bool:
    """?force=1 bypasses the once-per-period dedup so a message can be
    re-triggered on demand — e.g. to test that Telegram delivery works."""
    return request.args.get("force", "") in ("1", "true", "yes")


@flask_app.route("/cron/test", methods=["GET", "POST"])
def cron_test():
    """Diagnostic: unconditionally send a fixed test message and report whether
    delivery succeeded, plus how notifications are configured. Open this URL in
    a browser to check 'why didn't I get a Telegram message?' — no dedup, no
    dependence on whether there's a workout/nudge to send today."""
    if not _cron_authorized():
        return "forbidden", 403
    from datetime import datetime, timezone
    from notifier import notify_config
    cfg = notify_config()
    stamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
    sent = notify(f"✅ CoachxKeshav test message ({stamp}). "
                  f"If you can read this, scheduled notifications are working.")
    return jsonify({"sent": sent, "notify_config": cfg,
                    "hint": ("Delivered — notifications work." if sent else
                             "Not delivered — see notify_config for what's missing.")})


@flask_app.route("/cron/daily", methods=["GET", "POST"])
def cron_daily():
    if not _cron_authorized():
        return "forbidden", 403
    record_event("cron_daily")
    # Multiple scheduled attempts per day are expected (first ping wakes the
    # sleeping dyno) — only the first successful one sends.
    if not _cron_force() and job_done("cron_daily"):
        return jsonify({"sent": False, "skipped": "already ran today",
                        "hint": "add ?force=1 to re-send for testing"})
    msg = build_daily_nudge()
    if msg is None:
        mark_job_done("cron_daily")
        return jsonify({"sent": False,
                        "reason": "no nudge needed today (already trained, or profile not set up)"})
    sent = notify(msg)
    if sent:
        mark_job_done("cron_daily")
        clear_notify_failure()
        log.info("Daily cron: nudge sent")
        return jsonify({"sent": True, "message": msg})
    from notifier import notify_config
    record_notify_failure("cron_daily", notify_config().get("hint", "send failed"))
    log.warning("Daily cron: notify FAILED — channel not configured or send error")
    return jsonify({"sent": False, "reason": "notify failed — check notification config",
                    "notify_config": notify_config(), "message": msg})


@flask_app.route("/cron/weekly", methods=["GET", "POST"])
def cron_weekly():
    if not _cron_authorized():
        return "forbidden", 403
    record_event("cron_weekly")
    from agent_core import today as _today
    week_key = "{}-W{:02d}".format(*_today().isocalendar()[:2])
    if not _cron_force() and job_done("cron_weekly", week_key):
        return jsonify({"skipped": "already ran this week",
                        "hint": "add ?force=1 to re-send for testing"})
    msg  = build_weekly_report()
    sent = notify(msg)
    if sent:
        mark_job_done("cron_weekly", week_key)
    # Autonomous calorie tuning: adjust the daily target from the weigh-in
    # trend and tell the user what changed and why.
    adjustment = None
    try:
        from reports import auto_adjust_calories
        adjustment = auto_adjust_calories()
        if adjustment:
            notify(adjustment)
            log.info(adjustment)
    except Exception as e:
        log.error(f"Calorie auto-adjust failed: {e}")
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
    return jsonify({"sent": sent, "message": msg, "memory": consolidated,
                    "calorie_adjustment": adjustment})


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
    if not _cron_force() and job_done("cron_backup"):
        return jsonify({"skipped": "already ran today",
                        "hint": "add ?force=1 to re-send for testing"})
    from datetime import datetime, timezone
    try:
        dump = export_all()
        fname = f"coachx_backup_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
        sent = send_telegram_document(dump, fname, caption="CoachxKeshav weekly backup")
        if sent:
            mark_job_done("cron_backup")
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
    if not _cron_force() and job_done("cron_evening"):
        return jsonify({"skipped": "already ran today",
                        "hint": "add ?force=1 to re-send for testing"})
    from reports import build_evening_checkin
    from memory_core import summarize_today
    try:
        msg = build_evening_checkin()
        sent = notify(msg) if msg else False
        if sent or msg is None:
            mark_job_done("cron_evening")
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
# Telegram re-delivers an update if it doesn't get a fast 200, and a slow LLM
# turn can take long enough to trigger that — remember recent update_ids so a
# retry never double-processes (and double-logs) the same message.
_seen_tg_updates: deque = deque(maxlen=100)


@flask_app.route("/telegram", methods=["POST"])
def telegram_webhook():
    # Verify the secret token Telegram echoes back (set via setWebhook)
    if TELEGRAM_WEBHOOK_SECRET:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if got != TELEGRAM_WEBHOOK_SECRET:
            log.warning("Telegram: bad webhook secret")
            return "forbidden", 403

    update = request.get_json(silent=True) or {}
    update_id = update.get("update_id")
    if update_id is not None:
        if update_id in _seen_tg_updates:
            log.info(f"Telegram: skipping duplicate update {update_id}")
            return jsonify({"ok": True})
        _seen_tg_updates.append(update_id)
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
            caption = (msg.get("caption") or "").strip()
            if "progress" in caption.lower():
                # Progress photo, not a meal: store it for the Progress tab.
                log.info(f"Telegram progress photo from chat {chat_id}")
                sizes  = [p for p in photo if (p.get("file_size") or 0) < 600_000]
                chosen = sizes[-1] if sizes else photo[0]
                data   = download_telegram_file(chosen.get("file_id"))
                if data:
                    import base64
                    _save_progress_photo(base64.b64encode(data).decode())
                    reply = "📸 Progress photo saved — see it on the app's Progress tab."
                else:
                    reply = "Couldn't download that photo."
            else:
                log.info(f"Telegram photo from chat {chat_id}")
                largest = photo[-1]  # last entry is the highest resolution
                data    = download_telegram_file(largest.get("file_id"))
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
