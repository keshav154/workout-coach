"""
Expense tracker — MongoDB storage, categorization, summaries.
"""

import json
import os
import re
from datetime import date, datetime
from typing import Optional

from agent_core import _col

# ── Categories ────────────────────────────────────────────────────────────────
CATEGORIES = ["Food", "Transport", "Bills", "Shopping", "Health", "Entertainment", "Other"]

# ── MongoDB helpers ───────────────────────────────────────────────────────────
def log_expense(amount: float, description: str, category: str, note: str = "") -> dict:
    entry = {
        "date":        date.today().isoformat(),
        "amount":      amount,
        "description": description,
        "category":    category,
        "note":        note,
    }
    _col("expenses").insert_one(entry)
    entry.pop("_id", None)
    return entry


def get_expenses(month: Optional[str] = None) -> list:
    """Return expenses for given month (YYYY-MM) or current month."""
    if not month:
        month = date.today().strftime("%Y-%m")
    docs = list(_col("expenses").find({"date": {"$regex": f"^{month}"}}))
    for d in docs:
        d.pop("_id", None)
    return docs


def get_budget() -> dict:
    doc = _col("budget").find_one({"_id": "monthly"})
    if doc:
        doc.pop("_id", None)
        return doc
    return {}


def save_budget(category: str, amount: float) -> None:
    _col("budget").update_one(
        {"_id": "monthly"},
        {"$set": {category: amount}},
        upsert=True,
    )


def monthly_summary(month: Optional[str] = None) -> str:
    if not month:
        month = date.today().strftime("%Y-%m")
    expenses = get_expenses(month)
    budget   = get_budget()

    if not expenses:
        return f"No expenses logged for {month} yet."

    totals = {}
    for e in expenses:
        cat = e.get("category", "Other")
        totals[cat] = totals.get(cat, 0) + e["amount"]

    grand_total = sum(totals.values())
    lines = [f"Expenses for {month}", f"Total: Rs {grand_total:,.0f}", ""]

    for cat in CATEGORIES:
        if cat in totals:
            spent = totals[cat]
            bud   = budget.get(cat)
            if bud:
                pct  = int(spent / bud * 100)
                flag = " OVER BUDGET" if spent > bud else ""
                lines.append(f"{cat}: Rs {spent:,.0f} / Rs {bud:,.0f} ({pct}%){flag}")
            else:
                lines.append(f"{cat}: Rs {spent:,.0f}")

    return "\n".join(lines)


def today_summary() -> str:
    today = date.today().isoformat()
    docs  = list(_col("expenses").find({"date": today}))
    if not docs:
        return "No expenses logged today."
    total = sum(d["amount"] for d in docs)
    lines = [f"Today ({today}) — Rs {total:,.0f} total", ""]
    for d in docs:
        lines.append(f"  {d['category']}: Rs {d['amount']:,.0f} — {d['description']}")
    return "\n".join(lines)


# ── Expense detection ─────────────────────────────────────────────────────────
EXPENSE_TRIGGERS = re.compile(
    r"^(\$|rs\.?|rupees?|inr)?\s*\d|"
    r"\b(spent|paid|bought|purchased|expense|cost|charged|bill)\b",
    re.IGNORECASE,
)

COMMAND_PATTERN = re.compile(
    r"^!(expenses?|spending|budget|summary|monthly|today)",
    re.IGNORECASE,
)

def is_expense_message(text: str) -> bool:
    return bool(EXPENSE_TRIGGERS.search(text)) or bool(COMMAND_PATTERN.match(text))


# ── System prompt for expense parsing ────────────────────────────────────────
EXPENSE_SYSTEM_PROMPT = f"""You are an expense tracking assistant for an Indian user.
Today's date: {{today}}
Currency: Indian Rupees (Rs)
Categories: {", ".join(CATEGORIES)}

Your job:
1. Parse the user's message to extract: amount (number), description, category.
2. Always output a hidden block so the app can log it:

<LOG_EXPENSE>
{{"amount": 0.0, "description": "...", "category": "...", "note": ""}}
</LOG_EXPENSE>

3. Then reply in one short friendly line confirming what was logged.
   Example: "Logged Rs 500 for groceries under Food."

Categorization rules:
- Food: groceries, restaurant, swiggy, zomato, chai, snacks, dal, sabzi
- Transport: petrol, uber, ola, auto, bus, metro, cab, fuel
- Bills: electricity, internet, wifi, mobile recharge, rent, gas cylinder
- Shopping: clothes, amazon, flipkart, gadgets, shoes
- Health: medicine, doctor, gym, pharmacy, supplements
- Entertainment: movie, netflix, spotify, game, outing
- Other: anything that doesn't fit above

If amount is unclear, ask for clarification.
Keep replies short. Plain text only. Use Rs not ₹ symbol.
"""

def build_expense_prompt() -> str:
    return EXPENSE_SYSTEM_PROMPT.format(today=date.today().isoformat())


def try_parse_expense(text: str) -> Optional[dict]:
    match = re.search(r"<LOG_EXPENSE>\s*(\{.*?\})\s*</LOG_EXPENSE>", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


def get_workout_context(month: str) -> str:
    """Pull workout sessions from the same month for cross-agent insights."""
    try:
        sessions = list(_col("workout_log").find_one({"_id": "log"}) or {}).get("sessions", [])
        # handle find_one returning a dict
        doc = _col("workout_log").find_one({"_id": "log"}) or {}
        sessions = doc.get("sessions", [])
        month_sessions = [s for s in sessions if s.get("date", "").startswith(month)]
        if not month_sessions:
            return "No workout sessions logged this month."
        workout_dates = set(s["date"] for s in month_sessions)
        lines = [f"Workouts completed: {len(month_sessions)} sessions"]
        lines.append(f"Workout dates: {', '.join(sorted(workout_dates))}")
        return "\n".join(lines)
    except Exception:
        return "Workout data unavailable."


def build_review_prompt(month: Optional[str] = None) -> str:
    if not month:
        month = date.today().strftime("%Y-%m")

    expenses = get_expenses(month)
    budget   = get_budget()

    if not expenses:
        return None, f"No expenses found for {month}."

    # Build category totals
    totals = {}
    daily  = {}
    for e in expenses:
        cat  = e.get("category", "Other")
        d    = e.get("date", "")
        totals[cat] = totals.get(cat, 0) + e["amount"]
        daily[d]    = daily.get(d, 0) + e["amount"]

    grand_total   = sum(totals.values())
    days_with_spend = len(daily)
    avg_per_day   = grand_total / days_with_spend if days_with_spend else 0
    highest_day   = max(daily, key=daily.get) if daily else "N/A"
    highest_spend = daily.get(highest_day, 0)

    # Category breakdown text
    cat_lines = []
    for cat, amt in sorted(totals.items(), key=lambda x: -x[1]):
        bud = budget.get(cat)
        pct = int(amt / grand_total * 100)
        if bud:
            status = "OVER" if amt > bud else "ok"
            cat_lines.append(f"  {cat}: Rs {amt:,.0f} ({pct}% of total) — budget Rs {bud:,.0f} [{status}]")
        else:
            cat_lines.append(f"  {cat}: Rs {amt:,.0f} ({pct}% of total)")

    data_summary = f"""
Month: {month}
Total spent: Rs {grand_total:,.0f}
Days with spending: {days_with_spend}
Average per active day: Rs {avg_per_day:,.0f}
Highest spending day: {highest_day} (Rs {highest_spend:,.0f})

Category breakdown:
{chr(10).join(cat_lines)}

All transactions:
""" + "\n".join(
        f"  {e['date']} | {e['category']} | Rs {e['amount']:,.0f} | {e['description']}"
        for e in sorted(expenses, key=lambda x: x['date'])
    )

    workout_context = get_workout_context(month)

    prompt = f"""You are a personal finance and wellness advisor for an Indian user.
Analyze their expense AND workout data for {month} together to find cross-pattern insights.

EXPENSE DATA:
{data_summary}

WORKOUT DATA:
{workout_context}

Your review should include:
1. Overall spending assessment (2-3 lines)
2. Top 2 spending observations — patterns or surprises
3. Cross-insight: compare workout dates vs spending dates. Did they spend more on food delivery or entertainment on rest days or skipped workout days? Any pattern between consistency and spending?
4. One area to cut back with estimated savings
5. One thing they did well
6. One action tip for next month that covers both fitness and finances

Be specific to their actual numbers and dates. Conversational tone, under 280 words.
Use Rs not rupee symbol. Plain text only, no markdown symbols.
"""
    return prompt, None
