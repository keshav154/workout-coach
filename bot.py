"""
Workout Bot — Flask web UI + Discord bot on Render.
Data persisted in MongoDB Atlas (free tier).
"""

import functools
import logging
import os
import re
import threading

import discord
import requests
from flask import Flask, jsonify, render_template, request

from openai import OpenAI

from expense_core import (
    build_expense_prompt,
    build_review_prompt,
    get_budget,
    is_expense_message,
    log_expense,
    monthly_summary,
    save_budget,
    today_summary,
    try_parse_expense,
)
from agent_core import (
    PROGRAM,
    apply_memory_update,
    build_onboarding_prompt,
    build_system_prompt,
    get_last_session_for_day,
    get_next_day,
    load_history,
    load_log,
    load_memory,
    load_profile,
    profile_complete,
    reset_history,
    save_history,
    save_memory,
    save_profile,
    try_parse_log,
    try_parse_memory_update,
    try_parse_profile_update,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
GROQ_KEY        = os.environ["GROQ_API_KEY"].strip()
log.info(f"GROQ_API_KEY starts with: {GROQ_KEY[:8]}... length: {len(GROQ_KEY)}")
DISCORD_TOKEN   = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "").strip()
FLASK_SECRET    = os.environ.get("FLASK_SECRET", "change-me").strip()
WEB_PASSWORD    = os.environ.get("WEB_PASSWORD", "").strip()


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


groq = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")


# ── Shared agent call ─────────────────────────────────────────────────────────
def ask_agent(history: list, source: str = "web") -> tuple[str, dict | None, dict | None, dict | None]:
    profile  = load_profile()
    is_setup = profile_complete(profile)

    if not is_setup:
        system = build_onboarding_prompt()
    else:
        workout_log = load_log()
        mem         = load_memory()
        day         = get_next_day(workout_log)
        last        = get_last_session_for_day(workout_log, day)
        system      = build_system_prompt(day, last, workout_log, mem, profile)

    messages = [{"role": "system", "content": system}] + history

    resp = groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        temperature=0.7,
    )
    full = resp.choices[0].message.content or ""

    parsed_log     = None
    parsed_mem     = None
    parsed_profile = try_parse_profile_update(full)

    if parsed_profile:
        save_profile(parsed_profile)
        # Reload to build normal prompt on next turn
    elif is_setup:
        workout_log = load_log()
        mem         = load_memory()
        parsed_log  = try_parse_log(full)
        parsed_mem  = try_parse_memory_update(full)
        if parsed_log:
            from agent_core import save_session
            save_session(workout_log, parsed_log)
        if parsed_mem:
            apply_memory_update(mem, parsed_mem)
            save_memory(mem)

    # Strip all hidden blocks before showing to user
    display = re.sub(
        r"<(LOG_SESSION|UPDATE_MEMORY|SAVE_PROFILE)>.*?</\1>",
        "",
        full,
        flags=re.DOTALL,
    ).strip()

    return display, parsed_log, parsed_mem, parsed_profile


def log_suffix(parsed_log: dict | None) -> str:
    if not parsed_log:
        return ""
    parts = ["Session logged!"]
    bw = parsed_log.get("body_weight_kg")
    if bw:
        parts.append(f"Weight: {bw} kg")
    n = parsed_log.get("nutrition", {})
    if n.get("calories_eaten"):
        parts.append(f"{n['calories_eaten']} kcal | {n.get('protein_g','?')}g protein")
    return "\n\n" + " | ".join(parts)


# ── Flask web app ──────────────────────────────────────────────────────────────
flask_app = Flask(__name__)
flask_app.secret_key = FLASK_SECRET


@flask_app.route("/")
@require_auth
def index():
    return render_template("index.html")


@flask_app.route("/chat", methods=["POST"])
@require_auth
def chat():
    user_text = (request.json or {}).get("message", "").strip()
    if not user_text:
        return jsonify({"error": "empty message"}), 400

    history = load_history("web")
    history.append({"role": "user", "content": user_text})

    try:
        reply, parsed_log, _, parsed_profile = ask_agent(history, source="web")
    except Exception as e:
        log.error(f"Web agent error: {e}")
        return jsonify({"error": "AI error, please try again"}), 500

    reply += log_suffix(parsed_log)
    history.append({"role": "assistant", "content": reply})
    save_history("web", history)

    return jsonify({
        "reply": reply,
        "profile_saved": bool(parsed_profile),
    })


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
    p   = PROGRAM.get(day, {})
    return jsonify({"day": day, "name": p.get("name", ""), "focus": p.get("focus", "")})


@flask_app.route("/profile_status")
@require_auth
def profile_status():
    profile = load_profile()
    return jsonify({"complete": profile_complete(profile)})


@flask_app.route("/reset_profile", methods=["POST"])
@require_auth
def reset_profile():
    from agent_core import _col
    _col("profile").delete_one({"_id": "user"})
    reset_history("web")
    reset_history("discord")
    return jsonify({"ok": True})


@flask_app.route("/delete_last_session", methods=["POST"])
@require_auth
def delete_last_session():
    from agent_core import _col
    result = _col("workout_log").update_one(
        {"_id": "log"},
        {"$pop": {"sessions": 1}}
    )
    return jsonify({"ok": True, "modified": result.modified_count})


@flask_app.route("/health")
def health():
    return "OK", 200


# ── WhatsApp bot (Twilio) ──────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "").strip()  # e.g. whatsapp:+14155238886
ALLOWED_WHATSAPP_NUMBER = os.environ.get("ALLOWED_WHATSAPP_NUMBER", "").strip()  # e.g. whatsapp:+919876543210


@flask_app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    from twilio.twiml.messaging_response import MessagingResponse
    from twilio.request_validator import RequestValidator

    # Validate request is from Twilio
    if TWILIO_AUTH_TOKEN:
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        signature = request.headers.get("X-Twilio-Signature", "")
        url = request.url
        valid = validator.validate(url, request.form, signature)
        if not valid:
            log.warning("WhatsApp: invalid Twilio signature")
            return "Forbidden", 403

    from_number = request.form.get("From", "")
    user_text   = request.form.get("Body", "").strip()

    # Only respond to the allowed number
    if ALLOWED_WHATSAPP_NUMBER and from_number != ALLOWED_WHATSAPP_NUMBER:
        log.warning(f"WhatsApp: ignoring message from {from_number}")
        return str(MessagingResponse())

    log.info(f"WhatsApp message from {from_number}: {user_text[:50]}")

    twiml = MessagingResponse()
    cmd   = user_text.lower().strip()

    # ── Expense commands ──────────────────────────────────────────────────────
    if cmd in ("!expenses today", "!spending today", "!today"):
        twiml.message(today_summary())
        return str(twiml)

    if cmd in ("!expenses", "!expenses month", "!spending", "!monthly", "!summary"):
        twiml.message(monthly_summary())
        return str(twiml)

    if cmd.startswith("!budget "):
        # Format: !budget Food 5000
        parts = user_text.split()
        if len(parts) == 3:
            try:
                cat    = parts[1].capitalize()
                amount = float(parts[2])
                save_budget(cat, amount)
                twiml.message(f"Budget set: {cat} = Rs {amount:,.0f}/month")
                return str(twiml)
            except ValueError:
                pass
        twiml.message("Usage: !budget Food 5000")
        return str(twiml)

    if cmd in ("!review", "!review month", "!analyse", "!analyze"):
        prompt, err = build_review_prompt()
        if err:
            twiml.message(err)
            return str(twiml)
        try:
            resp = groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            review = resp.choices[0].message.content or "Could not generate review."
            for i in range(0, max(len(review), 1), 1500):
                twiml.message(review[i:i + 1500])
        except Exception as e:
            log.error(f"WhatsApp review error: {e}")
            twiml.message("Could not generate review. Try again.")
        return str(twiml)

    if cmd == "!help expense" or cmd == "!expense help":
        twiml.message(
            "Expense commands:\n"
            "Just type: spent 500 on groceries\n"
            "Or: paid 200 petrol\n"
            "Or: 1200 amazon\n\n"
            "!expenses today - today's spending\n"
            "!expenses - this month's summary\n"
            "!budget Food 5000 - set monthly budget\n"
            "!review - AI analysis of this month's spending\n"
        )
        return str(twiml)

    # ── Expense message (auto-detected) ───────────────────────────────────────
    if is_expense_message(user_text):
        history = load_history("whatsapp_expense")
        history.append({"role": "user", "content": user_text})

        try:
            messages = [{"role": "system", "content": build_expense_prompt()}] + history
            resp = groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.3,
            )
            full   = resp.choices[0].message.content or ""
            parsed = try_parse_expense(full)
            display = re.sub(r"<LOG_EXPENSE>.*?</LOG_EXPENSE>", "", full, flags=re.DOTALL).strip()

            if parsed and parsed.get("amount", 0) > 0:
                log_expense(
                    amount=float(parsed["amount"]),
                    description=parsed.get("description", user_text),
                    category=parsed.get("category", "Other"),
                    note=parsed.get("note", ""),
                )

            history.append({"role": "assistant", "content": display})
            save_history("whatsapp_expense", history[-10:])
            twiml.message(display)

        except Exception as e:
            log.error(f"WhatsApp expense error: {e}")
            twiml.message("Could not log expense. Try: spent 500 on groceries")

        return str(twiml)

    # ── Workout coach ─────────────────────────────────────────────────────────
    history = load_history("whatsapp")

    if cmd in ("!workout", "!start", "start", "hi", "hello"):
        reset_history("whatsapp")
        history = []
        user_msg = "What's my workout today?"
    elif cmd == "!done":
        user_msg = "I finished today's workout. Let's log it and go through my nutrition."
    elif cmd.startswith("!weight "):
        try:
            kg = float(user_text.split()[1])
            user_msg = f"My weight today is {kg} kg."
        except (ValueError, IndexError):
            twiml.message("Usage: !weight 97.5")
            return str(twiml)
    elif cmd == "!reset":
        reset_history("whatsapp")
        twiml.message("Conversation reset! Send 'hi' to begin.")
        return str(twiml)
    elif cmd in ("!help", "help"):
        twiml.message(
            "Workout commands:\n"
            "!workout - today's workout\n"
            "!done - log session + nutrition\n"
            "!weight 97.5 - log weight\n"
            "!reset - fresh conversation\n\n"
            "Expense tracking:\n"
            "spent 500 on groceries\n"
            "paid 200 petrol\n"
            "!expenses - monthly summary\n"
            "!expense help - more expense commands"
        )
        return str(twiml)
    else:
        user_msg = user_text

    history.append({"role": "user", "content": user_msg})

    try:
        reply, parsed_log, _, _ = ask_agent(history, source="whatsapp")
    except Exception as e:
        log.error(f"WhatsApp agent error: {e}")
        twiml.message("Something went wrong. Please try again.")
        return str(twiml)

    reply += log_suffix(parsed_log)
    history.append({"role": "assistant", "content": reply})
    save_history("whatsapp", history)

    for i in range(0, max(len(reply), 1), 1500):
        twiml.message(reply[i:i + 1500])
    return str(twiml)


# ── Discord bot ────────────────────────────────────────────────────────────────
HELP_MSG = """Commands:
!workout  - today's workout
!done     - log session + nutrition
!weight 97.5 - log your weight
!summary  - last session recap
!reset    - fresh conversation
!help     - this menu

Or just type anything to chat with your coach!"""


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
            history = load_history("discord")

            if text in ("!workout", "!start"):
                reset_history("discord")
                history = []
                user_msg = "What's my workout today? Also ask me my weight."

            elif text == "!done":
                user_msg = "I finished today's workout. Let's log it and go through my nutrition."

            elif text.startswith("!weight "):
                try:
                    kg = float(text.split()[1])
                    user_msg = f"My weight today is {kg} kg."
                except (ValueError, IndexError):
                    await message.channel.send("Usage: !weight 97.5")
                    return

            elif text == "!summary":
                profile = load_profile()
                if not profile_complete(profile):
                    await message.channel.send("Complete your profile setup first by chatting with me!")
                    return
                workout_log = load_log()
                sessions = workout_log.get("sessions", [])
                if not sessions:
                    await message.channel.send("No sessions logged yet. Start with !workout")
                    return
                last = sessions[-1]
                day  = last.get("day", "?")
                p    = PROGRAM.get(day, {})
                lines = [f"Last session: Day {day} - {p.get('name','')} ({last.get('date','?')})", ""]
                bw = last.get("body_weight_kg")
                if bw:
                    lines.append(f"Weight: {bw} kg")
                for ex in last.get("exercises", []):
                    lines.append(f"  {ex['name']}: {ex.get('weight','?')}kg x {ex.get('reps_done','?')} reps")
                n = last.get("nutrition", {})
                if n.get("calories_eaten"):
                    lines += ["", f"Nutrition: {n['calories_eaten']} kcal | {n.get('protein_g','?')}g protein",
                              f"Burnt: {n.get('calories_burnt','?')} kcal | Net: {n.get('net_calories','?')} kcal"]
                await message.channel.send("\n".join(lines))
                return

            elif text == "!reset":
                reset_history("discord")
                await message.channel.send("Conversation reset. Send !workout to begin.")
                return

            elif text in ("!help", "!commands"):
                await message.channel.send(HELP_MSG)
                return

            elif text.startswith("!"):
                await message.channel.send(HELP_MSG)
                return

            else:
                user_msg = text

            history.append({"role": "user", "content": user_msg})

            try:
                reply, parsed_log, _, _ = ask_agent(history, source="discord")
            except Exception as e:
                log.error(f"Discord agent error: {e}")
                await message.channel.send("Something went wrong. Please try again.")
                return

            reply += log_suffix(parsed_log)
            history.append({"role": "assistant", "content": reply})
            save_history("discord", history)

            for i in range(0, max(len(reply), 1), 1900):
                await message.channel.send(reply[i:i + 1900])

    return client


# ── Start both services ────────────────────────────────────────────────────────
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
    discord_thread = threading.Thread(target=run_discord, daemon=True)
    discord_thread.start()
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
