"""
Single message router shared by web, WhatsApp, and Discord.

process_message(text, source) is the entire interface — there is no command
syntax. Workouts, weight, nutrition, expenses, goals, budgets, check-ins, and
data questions are all handled by one unified LLM brain via natural language,
hidden action blocks, and tool calls. `source` is the history-key prefix
(e.g. "web", "whatsapp", "discord").
"""

import json
import logging
import re

from llm import chat, reason_loop, transcribe, vision
from agent_core import (
    apply_memory_update,
    build_onboarding_prompt,
    build_system_prompt,
    detect_prs,
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
    save_session,
    today_iso,
    try_parse_log,
    try_parse_memory_update,
    try_parse_profile_update,
)
from expense_core import log_expense, monthly_summary, save_budget, try_parse_expense
from trust import record_audit, undo_last, validate_expense, validate_session
from ask_core import TOOLS as READ_TOOLS, TOOL_IMPLS as READ_IMPLS
from progression import (
    clear_autodeload_flag,
    format_autodeload_block,
    format_progression_block,
    get_autodeload_flags,
)
from nutrition import format_nutrition_block, log_meal
from checkin import format_checkin_block, save_checkin
from goals import clear_goals, format_goals_block, set_goal

log = logging.getLogger(__name__)

HELP_MSG = (
    "There's nothing to memorize — just talk to me the way you'd talk to a coach. For example:\n\n"
    "\"what's my workout today?\"\n"
    "\"done, benched 18kg for 10\"\n"
    "\"slept 6 hours, feeling sore\"\n"
    "\"I want to reach 90kg by September\"\n"
    "\"spent 500 on groceries\"\n"
    "\"how much did I spend on food this month?\"\n"
    "\"give me a proper review of my spending\"\n"
    "\"cap my food budget at 8000 a month\"\n"
    "\"how's my progress, any plateaus?\"\n"
    "\"undo that\"\n\n"
    "I'll ask you questions when I need something, and I'll speak up on my own "
    "if I notice something worth flagging — you never need a specific phrase or command."
)


def _parse_block(tag: str, text: str) -> dict | None:
    m = re.search(rf"<{tag}>\s*(\{{.*?\}})\s*</{tag}>", text, re.DOTALL | re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


def _strip_hidden(text: str) -> str:
    """Strip private reasoning + control blocks, robust across models/formats."""
    # Primary mechanism: keep only what follows the explicit reply marker.
    if "===REPLY===" in text:
        text = text.rsplit("===REPLY===", 1)[-1]
    # Remove well-formed control/reasoning blocks (case-insensitive).
    text = re.sub(
        r"<(LOG_SESSION|UPDATE_MEMORY|SAVE_PROFILE|LOG_EXPENSE|LOG_MEAL|CHECKIN|SET_GOAL|SET_BUDGET|UPDATE_PROFILE|UNDO|CLEAR_GOALS|THINK|THINKING)>.*?</\1>",
        "", text, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<UNDO\s*/?>", "", text, flags=re.IGNORECASE)          # self-closing undo
    text = re.sub(r"<CLEAR_GOALS\s*/?>", "", text, flags=re.IGNORECASE)   # self-closing clear-goals
    # Fallback: drop an unclosed/loose reasoning block if the marker was absent.
    if re.search(r"<\s*think", text, re.IGNORECASE):
        text = re.sub(r"<\s*think.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</?\s*think\w*\s*>", "", text, flags=re.IGNORECASE)  # stray tags
    return text.strip()


# ── Workout agent ─────────────────────────────────────────────────────────────
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
        extra = "\n\n".join(filter(None, [
            format_checkin_block(log=workout_log),
            format_progression_block(workout_log),
            format_autodeload_block(),
            format_goals_block(),
            format_nutrition_block(),
            "SPENDING THIS MONTH (for any money questions):\n" + monthly_summary(),
        ]))
        system = build_system_prompt(day, last, workout_log, mem, profile, extra_context=extra)

    base_messages = [{"role": "system", "content": system}] + history
    if is_setup:
        # Tool-grounded: let the model fetch REAL data from the DB and treat it
        # as fact. Falls back to a plain call if the provider lacks tool support.
        try:
            full = reason_loop(list(base_messages), READ_TOOLS, READ_IMPLS,
                               max_steps=4, temperature=0.5)
        except Exception as e:
            log.warning(f"Coach tool loop failed ({e}); using plain call.")
            full = chat([{"role": "system", "content": system}] + history, temperature=0.7)
    else:
        full = chat(base_messages, temperature=0.7)

    parsed_log     = None
    parsed_mem     = None
    parsed_profile = try_parse_profile_update(full)
    pr_msgs        = []

    validation_warning = ""
    expense_suffix     = ""
    if not is_setup:
        # SAVE_PROFILE is only valid during onboarding — never let an
        # established user's profile be overwritten by a hallucinated block.
        if parsed_profile:
            save_profile(parsed_profile)
    else:
        parsed_profile = None
        workout_log = load_log()   # state BEFORE this session is saved
        mem         = load_memory()
        parsed_log  = try_parse_log(full)
        parsed_mem  = try_parse_memory_update(full)
        if parsed_log:
            ok, reason, cleaned = validate_session(parsed_log)
            if ok:
                # Code is the source of truth for day & date — never trust the LLM.
                cleaned["date"] = today_iso()
                cleaned["day"]  = day
                parsed_log = cleaned
                pr_msgs = detect_prs(workout_log, parsed_log)  # compare vs history first
                save_session(workout_log, parsed_log)
                record_audit("session",
                             f"Day {parsed_log.get('day')} on {parsed_log.get('date')}",
                             ref={"date": parsed_log.get("date"), "day": parsed_log.get("day")})
                # Autonomous plateau flags are one-shot: clear once the flagged
                # exercise has actually been logged with the deloaded weight.
                if get_autodeload_flags():
                    logged_names = {e.get("name") for e in parsed_log.get("exercises", [])}
                    for name in logged_names:
                        clear_autodeload_flag(name)
            else:
                parsed_log = None          # don't save bad data
                validation_warning = reason
        if parsed_mem:
            apply_memory_update(mem, parsed_mem)
        if pr_msgs:
            apply_memory_update(mem, {"personal_records": pr_msgs})
        if parsed_mem or pr_msgs:
            save_memory(mem)

        # Natural-language actions (check-in, goal, undo, profile tweak)
        ck = _parse_block("CHECKIN", full)
        if ck and any(ck.get(k) is not None for k in ("sleep_hours", "energy", "soreness")):
            save_checkin(sleep_hours=ck.get("sleep_hours"), energy=ck.get("energy"),
                         soreness=ck.get("soreness"))
            expense_suffix += "\n\n✅ Check-in saved."
        gl = _parse_block("SET_GOAL", full)
        if gl and gl.get("target"):
            set_goal(kind=gl.get("kind", "weight"), target=gl["target"],
                     by_date=gl.get("by_date"), exercise=gl.get("exercise"))
            expense_suffix += "\n\n🎯 Goal set."
        if re.search(r"<UNDO\s*/?>|<UNDO>\s*</UNDO>", full, re.IGNORECASE):
            expense_suffix += "\n\n" + undo_last()
        up = _parse_block("UPDATE_PROFILE", full)
        if up and up.get("days_per_week"):
            try:
                profile["days_per_week"] = int(up["days_per_week"])
                save_profile(profile)
                expense_suffix += f"\n\n📅 Now training {profile['days_per_week']} days/week."
            except (ValueError, TypeError):
                pass
        meal = _parse_block("LOG_MEAL", full)
        if meal and meal.get("description"):
            log_meal(meal["description"], meal.get("calories", 0), meal.get("protein", 0))
            expense_suffix += "\n\n🍽️ Meal logged."
        bud = _parse_block("SET_BUDGET", full)
        if bud and bud.get("category") and bud.get("amount"):
            try:
                save_budget(str(bud["category"]).capitalize(), float(bud["amount"]))
                expense_suffix += f"\n\n💰 Budget set: {bud['category']} = Rs {float(bud['amount']):,.0f}/month."
            except (ValueError, TypeError):
                pass
        if re.search(r"<CLEAR_GOALS\s*/?>|<CLEAR_GOALS>\s*</CLEAR_GOALS>", full, re.IGNORECASE):
            n = clear_goals()
            expense_suffix += f"\n\n🗑️ Cleared {n} goal(s)."

        # Unified brain may also log an expense in the same turn
        parsed_expense = try_parse_expense(full)
        if parsed_expense and parsed_expense.get("amount", 0) > 0:
            ok, reason = validate_expense(parsed_expense["amount"])
            if ok:
                entry = log_expense(
                    amount=float(parsed_expense["amount"]),
                    description=parsed_expense.get("description", ""),
                    category=parsed_expense.get("category", "Other"),
                    note=parsed_expense.get("note", ""),
                )
                record_audit("expense",
                             f"Rs {entry['amount']:,.0f} {entry['category']} — {entry['description']}",
                             ref=entry.get("id"))
                expense_suffix += f"\n\n💸 Logged Rs {entry['amount']:,.0f} under {entry['category']}."
            else:
                validation_warning = (validation_warning + " " + reason).strip()

    display = _strip_hidden(full)

    if pr_msgs:
        display += "\n\n" + "\n".join(f"🎉 {m}" for m in pr_msgs)
    if expense_suffix:
        display += expense_suffix
    if validation_warning:
        display += f"\n\n⚠️ {validation_warning}"

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


def _handle_workout_message(text: str, cmd: str, source: str) -> str:
    history = load_history(source)

    if cmd in ("start", "hi", "hello", "hey") and not history:
        # First-ever greeting starts things off; later greetings are normal chat.
        user_msg = "What's my workout today?"
    else:
        user_msg = text

    history.append({"role": "user", "content": user_msg})
    try:
        reply, parsed_log, _, _ = ask_agent(history, source=source)
    except Exception as e:
        log.error(f"Agent error ({source}): {e}")
        return "Something went wrong. Please try again."

    reply += log_suffix(parsed_log)
    history.append({"role": "assistant", "content": reply})
    save_history(source, history)
    return reply


# ── Voice notes (Groq Whisper) ────────────────────────────────────────────────
def transcribe_and_process(audio_bytes: bytes, source: str = "telegram", filename: str = "voice.ogg") -> str:
    """Transcribe a voice note, run it through the normal router, echo what was heard."""
    try:
        text = transcribe(audio_bytes, filename=filename)
    except Exception as e:
        log.error(f"Transcription error: {e}", exc_info=True)
        return "Couldn't understand that voice note. Please try again or type it."
    if not text:
        return "I couldn't hear anything in that voice note."
    reply = process_message(text, source=source)
    return f'🎤 "{text}"\n\n{reply}'


# ── Meal photo logging (Groq vision) ──────────────────────────────────────────
MEAL_PROMPT = (
    "You are a nutrition assistant for an Indian vegetarian user. "
    "Look at this meal photo and identify each food item with estimated calories and "
    "protein using typical Indian portion sizes. Then end with a line:\n"
    "TOTAL: <kcal> kcal | <grams>g protein\n"
    "Be concise. Plain text only, no markdown."
)


def analyze_meal_photo(image_bytes: bytes, caption: str = "", source: str = "telegram",
                       mime: str = "image/jpeg") -> str:
    """Estimate a meal's calories/protein from a photo and remember it for later tally."""
    prompt = MEAL_PROMPT + (f"\nUser note about this meal: {caption}" if caption else "")
    try:
        result = vision(image_bytes, prompt, mime=mime)
    except Exception as e:
        log.error(f"Meal vision error: {e}", exc_info=True)
        return "Couldn't analyze that photo. You can type what you ate instead."
    if not result.strip():
        return "Couldn't read that meal photo. Try a clearer shot or type what you ate."

    # Parse "TOTAL: <kcal> kcal | <grams>g protein" so this becomes a real,
    # queryable meal entry instead of just chat text.
    m = re.search(r"TOTAL:\s*([\d.]+)\s*kcal\s*\|\s*([\d.]+)\s*g", result, re.IGNORECASE)
    if m:
        cal, prot = float(m.group(1)), float(m.group(2))
        log_meal(caption or "Meal (from photo)", cal, prot, note=result[:300])

    # Also store in the coach's history for conversational continuity
    history = load_history(source)
    history.append({"role": "user", "content": f"I ate this meal (estimated from a photo): {result}"})
    history.append({"role": "assistant", "content": "Noted your meal."})
    save_history(source, history)
    return result


# ── Main router ───────────────────────────────────────────────────────────────
def process_message(text: str, source: str = "web") -> str:
    """Route any message to the right handler and return reply text."""
    text = (text or "").strip()
    if not text:
        return ""
    cmd = text.lower().strip()

    # There is no command syntax in this app. Two things are handled outside
    # the LLM entirely because they're deterministic client operations, not
    # coaching decisions: asking what the agent can do, and wiping the
    # conversation (a destructive action safer as a fixed trigger than an
    # LLM judgment call). Everything else — workouts, weight, nutrition,
    # expenses, goals, budgets, check-ins, progress questions, undo — is
    # handled by the unified brain via natural language and hidden action
    # blocks, or via tool calls for data questions.
    if cmd in ("help", "what can you do", "what can you do?"):
        return HELP_MSG
    if cmd in ("reset", "start over", "reset conversation", "clear our conversation",
              "forget this conversation", "start a new conversation"):
        reset_history(source)
        return "Conversation reset! Send 'hi' to begin."

    return _handle_workout_message(text, cmd, source)
