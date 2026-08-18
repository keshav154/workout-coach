"""
Expense tracker — MongoDB storage, categorization, summaries.
"""

import json
import re
from typing import Optional

from agent_core import _col, today, today_iso

# ── Categories ────────────────────────────────────────────────────────────────
CATEGORIES = ["Food", "Transport", "Bills", "Shopping", "Health", "Entertainment", "Other"]

# ── MongoDB helpers ───────────────────────────────────────────────────────────
def log_expense(amount: float, description: str, category: str, note: str = "") -> dict:
    entry = {
        "date":        today_iso(),
        "amount":      amount,
        "description": description,
        "category":    category,
        "note":        note,
    }
    result = _col("expenses").insert_one(entry)
    entry["id"] = str(result.inserted_id)
    entry.pop("_id", None)
    return entry


def get_expenses(month: Optional[str] = None) -> list:
    """Return expenses for given month (YYYY-MM) or current month."""
    if not month:
        month = today().strftime("%Y-%m")
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
        month = today().strftime("%Y-%m")
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


def _all_expenses() -> list:
    docs = list(_col("expenses").find())
    for d in docs:
        d.pop("_id", None)
    return docs


def spending_forecast(month: Optional[str] = None) -> dict:
    """Project month-end spend from the pace so far, vs last month. For the
    current month, extrapolates from days elapsed; for a past month, the total
    is final. Returns {} when there's nothing to project."""
    import calendar

    now = today()
    if not month:
        month = now.strftime("%Y-%m")
    try:
        y, m = (int(x) for x in month.split("-"))
    except (ValueError, AttributeError):
        return {}

    spent = round(sum(e["amount"] for e in get_expenses(month)))
    days_in_month = calendar.monthrange(y, m)[1]
    is_current = (y, m) == (now.year, now.month)
    days_elapsed = now.day if is_current else days_in_month
    projected = (round(spent / days_elapsed * days_in_month)
                 if is_current and days_elapsed > 0 else spent)

    # Previous month total for comparison.
    pm_y, pm_m = (y - 1, 12) if m == 1 else (y, m - 1)
    last_month = round(sum(e["amount"] for e in get_expenses(f"{pm_y:04d}-{pm_m:02d}")))

    if not spent and not last_month:
        return {}
    return {
        "month": month, "spent": spent, "projected": projected,
        "days_elapsed": days_elapsed, "days_in_month": days_in_month,
        "is_current": is_current, "last_month": last_month,
        "delta_vs_last": projected - last_month,
        "days_left": max(0, days_in_month - days_elapsed) if is_current else 0,
    }


def detect_recurring(min_months: int = 2, lookback_months: int = 4) -> list[dict]:
    """Spot repeating charges (subscriptions, rent, gym) — a description that
    recurs across multiple months at a similar amount. Returns
    [{description, category, monthly, occurrences, months}] sorted by cost."""
    from statistics import median

    now = today()
    cutoff_y, cutoff_m = now.year, now.month - (lookback_months - 1)
    while cutoff_m <= 0:
        cutoff_m += 12
        cutoff_y -= 1
    cutoff = f"{cutoff_y:04d}-{cutoff_m:02d}"

    groups: dict[str, dict] = {}
    for e in _all_expenses():
        date_s = e.get("date", "")
        if date_s[:7] < cutoff:
            continue
        desc = " ".join((e.get("description") or "").lower().split())
        if not desc:
            continue
        g = groups.setdefault(desc, {"amounts": [], "months": set(),
                                     "category": e.get("category", "Other"),
                                     "label": (e.get("description") or "").strip()})
        g["amounts"].append(float(e.get("amount") or 0))
        g["months"].add(date_s[:7])

    out = []
    for desc, g in groups.items():
        if len(g["months"]) < min_months:
            continue
        out.append({
            "description": g["label"] or desc,
            "category": g["category"],
            "monthly": round(median(g["amounts"])),
            "occurrences": len(g["amounts"]),
            "months": len(g["months"]),
        })
    out.sort(key=lambda r: -r["monthly"])
    return out


def try_parse_expense(text: str) -> Optional[dict]:
    match = re.search(r"<LOG_EXPENSE>\s*(\{.*?\})\s*</LOG_EXPENSE>", text, re.DOTALL)
    if match:
        try:
            raw = json.loads(match.group(1))
            # Normalize keys — strip extra surrounding quotes the LLM sometimes adds
            return {k.strip('"\'').strip(): v for k, v in raw.items()}
        except Exception:
            pass
    # Fallback: try to extract amount directly from text
    amount_match = re.search(r'["\']?amount["\']?\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)', text, re.IGNORECASE)
    if amount_match:
        try:
            return {"amount": float(amount_match.group(1)), "description": "", "category": "Other", "note": ""}
        except Exception:
            pass
    return None


def get_workout_context(month: str) -> str:
    """Pull workout sessions from the same month for cross-agent insights."""
    try:
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
        month = today().strftime("%Y-%m")

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
