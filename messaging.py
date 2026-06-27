"""
Single message router shared by web, WhatsApp, and Discord.

process_message(text, source) handles every kind of input — expense commands,
expense logging, workout commands, and conversational coaching — and returns
the reply text. `source` is the history-key prefix (e.g. "web", "whatsapp",
"discord"); expense history is stored under f"{source}_expense".
"""

import logging
import re

from llm import chat
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
    save_session,
    try_parse_log,
    try_parse_memory_update,
    try_parse_profile_update,
)
from expense_core import (
    build_expense_prompt,
    build_review_prompt,
    is_expense_message,
    log_expense,
    monthly_summary,
    save_budget,
    today_summary,
    try_parse_expense,
)

log = logging.getLogger(__name__)

HELP_MSG = (
    "Workout commands:\n"
    "!workout - today's workout\n"
    "!done - log session + nutrition\n"
    "!weight 97.5 - log weight\n"
    "!summary - last session recap\n"
    "!reset - fresh conversation\n\n"
    "Expense tracking:\n"
    "spent 500 on groceries\n"
    "paid 200 petrol\n"
    "!expenses - monthly summary\n"
    "!review - AI analysis of your spending\n"
    "!expense help - more expense commands"
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
        system      = build_system_prompt(day, last, workout_log, mem, profile)

    messages = [{"role": "system", "content": system}] + history
    full     = chat(messages, temperature=0.7)

    parsed_log     = None
    parsed_mem     = None
    parsed_profile = try_parse_profile_update(full)
    pr_msgs        = []

    if parsed_profile:
        save_profile(parsed_profile)
    elif is_setup:
        workout_log = load_log()   # state BEFORE this session is saved
        mem         = load_memory()
        parsed_log  = try_parse_log(full)
        parsed_mem  = try_parse_memory_update(full)
        if parsed_log:
            pr_msgs = detect_prs(workout_log, parsed_log)  # compare vs history first
            save_session(workout_log, parsed_log)
        if parsed_mem:
            apply_memory_update(mem, parsed_mem)
        if pr_msgs:
            apply_memory_update(mem, {"personal_records": pr_msgs})
        if parsed_mem or pr_msgs:
            save_memory(mem)

    display = re.sub(
        r"<(LOG_SESSION|UPDATE_MEMORY|SAVE_PROFILE)>.*?</\1>",
        "",
        full,
        flags=re.DOTALL,
    ).strip()

    if pr_msgs:
        display += "\n\n" + "\n".join(f"🎉 {m}" for m in pr_msgs)

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


def _handle_expense_message(text: str, source: str) -> str:
    key     = f"{source}_expense"
    history = load_history(key)
    history.append({"role": "user", "content": text})
    try:
        messages = [{"role": "system", "content": build_expense_prompt()}] + history
        full     = chat(messages, temperature=0.3)
        parsed   = try_parse_expense(full)
        display  = re.sub(r"<LOG_EXPENSE>.*?</LOG_EXPENSE>", "", full, flags=re.DOTALL).strip()

        if parsed and parsed.get("amount", 0) > 0:
            log_expense(
                amount=float(parsed["amount"]),
                description=parsed.get("description", text),
                category=parsed.get("category", "Other"),
                note=parsed.get("note", ""),
            )
            reply = display or f"Logged Rs {parsed['amount']:,.0f} under {parsed.get('category','Other')}."
        else:
            log.warning(f"No expense parsed from: {full[:200]}")
            reply = display or "Got it. Type !expenses to see your summary."

        history.append({"role": "assistant", "content": reply})
        save_history(key, history[-10:])
        return reply
    except Exception as e:
        log.error(f"Expense error: {e}", exc_info=True)
        return "Could not log expense. Try: spent 500 on groceries"


def _handle_workout_message(text: str, cmd: str, source: str) -> str:
    history = load_history(source)

    if cmd in ("!workout", "!start", "start", "hi", "hello"):
        reset_history(source)
        history = []
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

    # Workout no-agent commands
    if cmd == "!summary":
        return _last_session_summary()
    if cmd in ("!help", "help", "!commands"):
        return HELP_MSG
    if cmd == "!reset":
        reset_history(source)
        reset_history(f"{source}_expense")
        return "Conversation reset! Send 'hi' to begin."

    # Auto-detected expense logging
    if is_expense_message(text):
        return _handle_expense_message(text, source)

    # Conversational coaching
    return _handle_workout_message(text, cmd, source)
