"""
Single message router shared by web, WhatsApp, and Discord.

process_message(text, source) handles every kind of input — expense commands,
expense logging, workout commands, and conversational coaching — and returns
the reply text. `source` is the history-key prefix (e.g. "web", "whatsapp",
"discord"); expense history is stored under f"{source}_expense".
"""

import json
import logging
import re

from llm import chat, reason_loop, transcribe, vision
from agent_core import (
    PROGRAM,
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
from expense_core import (
    build_review_prompt,
    log_expense,
    monthly_summary,
    save_budget,
    today_summary,
    try_parse_expense,
)
from trust import record_audit, undo_last, validate_expense, validate_session
from ask_core import TOOLS as READ_TOOLS, TOOL_IMPLS as READ_IMPLS, answer_question
from monitor import get_status
from progression import format_progression_block, progress_summary
from checkin import format_checkin_block, parse_checkin_command, save_checkin
from goals import (
    clear_goals,
    format_goals_block,
    goals_status,
    parse_goal_command,
    set_goal,
)

log = logging.getLogger(__name__)

HELP_MSG = (
    "Just talk to me normally — no commands needed. For example:\n\n"
    "\"what's my workout today?\"\n"
    "\"done, benched 18kg for 10\"\n"
    "\"slept 6 hours, feeling sore\"\n"
    "\"I want to reach 90kg by September\"\n"
    "\"spent 500 on groceries\"\n"
    "\"how much did I spend on food this month?\"\n"
    "\"how's my progress / any plateaus?\"\n"
    "\"undo that\"\n\n"
    "Optional shortcuts if you prefer: !workout, !done, !progress, "
    "!goals, !expenses, !review, !undo, !status, !reset."
)

EXPENSE_HELP = (
    "Expense commands:\n"
    "Just type: spent 500 on groceries\n"
    "Or: paid 200 petrol\n"
    "Or: 1200 amazon\n\n"
    "!expenses today - today's spending\n"
    "!expenses - this month's summary\n"
    "!budget Food 5000 - set monthly budget\n"
    "!review - AI analysis of this month's spending"
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
        r"<(LOG_SESSION|UPDATE_MEMORY|SAVE_PROFILE|LOG_EXPENSE|CHECKIN|SET_GOAL|UPDATE_PROFILE|UNDO|THINK|THINKING)>.*?</\1>",
        "", text, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<UNDO\s*/?>", "", text, flags=re.IGNORECASE)   # self-closing undo
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
            format_goals_block(),
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
    if parsed_profile:
        save_profile(parsed_profile)
    elif is_setup:
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
                             f"Day {parsed_log.get('day')} on {parsed_log.get('date')}")
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
                expense_suffix = f"\n\n💸 Logged Rs {entry['amount']:,.0f} under {entry['category']}."
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


# ── Command handlers ──────────────────────────────────────────────────────────
def _last_session_summary() -> str:
    if not profile_complete(load_profile()):
        return "Complete your profile setup first by chatting with me!"
    sessions = load_log().get("sessions", [])
    if not sessions:
        return "No sessions logged yet. Send !workout to begin."
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
    return "\n".join(lines)


def _generate_review() -> str:
    prompt, err = build_review_prompt()
    if err:
        return err
    try:
        return chat([{"role": "user", "content": prompt}], temperature=0.7) or "Could not generate review."
    except Exception as e:
        log.error(f"Review error: {e}")
        return "Could not generate review. Try again."


def _handle_workout_message(text: str, cmd: str, source: str) -> str:
    history = load_history(source)

    if cmd in ("!workout", "!start"):
        # Explicit workout command — keep history for continuity, just ask.
        user_msg = "What's my workout today?"
    elif cmd in ("start", "hi", "hello", "hey") and not history:
        # First-ever greeting starts things off; later greetings are normal chat.
        user_msg = "What's my workout today?"
    elif cmd == "!done":
        user_msg = "I finished today's workout. Let's log it and go through my nutrition."
    elif cmd.startswith("!weight "):
        try:
            kg = float(text.split()[1])
            user_msg = f"My weight today is {kg} kg."
        except (ValueError, IndexError):
            return "Usage: !weight 97.5"
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

    # Store in the coach's history so it counts toward today's nutrition tally
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

    # Expense summary commands
    if cmd in ("!expenses today", "!spending today", "!today"):
        return today_summary()
    if cmd in ("!expenses", "!expenses month", "!spending", "!monthly"):
        return monthly_summary()
    if cmd.startswith("!budget "):
        parts = text.split()
        if len(parts) == 3:
            try:
                save_budget(parts[1].capitalize(), float(parts[2]))
                return f"Budget set: {parts[1].capitalize()} = Rs {float(parts[2]):,.0f}/month"
            except ValueError:
                pass
        return "Usage: !budget Food 5000"
    if cmd in ("!review", "!analyse", "!analyze"):
        return _generate_review()
    if cmd in ("!help expense", "!expense help"):
        return EXPENSE_HELP

    # Cross-domain recall + system commands
    if cmd.startswith("!ask"):
        return answer_question(text[4:].strip())
    if cmd == "!undo":
        return undo_last()
    if cmd == "!status":
        return get_status()

    # Progression, check-in, goals
    if cmd in ("!progress", "!progression"):
        return progress_summary()
    if cmd.startswith("!checkin"):
        parsed = parse_checkin_command(text)
        if not parsed:
            return ("Daily check-in — log sleep, energy, soreness:\n"
                    "!checkin 7 8 3   (sleep hours, energy 1-10, soreness 1-10)\n"
                    "or !checkin sleep=7 energy=8 soreness=3")
        save_checkin(**parsed)
        return "Check-in saved. " + format_checkin_block()
    if cmd in ("!goals", "!goal") and len(text.split()) == 1:
        return goals_status()
    if cmd.startswith("!goal clear"):
        n = clear_goals()
        return f"Cleared {n} goal(s)."
    if cmd.startswith("!goal "):
        parsed = parse_goal_command(text)
        if not parsed:
            return ("Set a goal:\n"
                    "!goal weight 90 by 2026-09-01\n"
                    "!goal lift bench 24\n"
                    "!goals to view, !goal clear to reset")
        set_goal(**parsed)
        return "Goal set!\n\n" + goals_status()

    # Workout no-agent commands
    if cmd.startswith("!days"):
        parts = text.split()
        if len(parts) == 2 and parts[1].isdigit() and 1 <= int(parts[1]) <= 7:
            profile = load_profile()
            if not profile_complete(profile):
                return "Finish your profile setup first by chatting with me!"
            profile["days_per_week"] = int(parts[1])
            save_profile(profile)
            return f"Updated: training {parts[1]} days per week. Your weekly target now reflects this."
        return "Usage: !days 5"
    if cmd == "!summary":
        return _last_session_summary()
    if cmd in ("!help", "help", "!commands"):
        return HELP_MSG
    if cmd == "!reset":
        reset_history(source)
        reset_history(f"{source}_expense")
        return "Conversation reset! Send 'hi' to begin."

    # Everything else goes to the single unified brain, which sees the full
    # conversation history and decides intent (workout / weight / nutrition /
    # expense) with full context — no brittle regex routing.
    return _handle_workout_message(text, cmd, source)
