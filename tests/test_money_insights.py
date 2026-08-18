"""Month-end spend forecast + recurring-charge detection."""

import expense_core as E
from agent_core import today


def _month(offset):
    """YYYY-MM for `offset` months before the current one."""
    y, m = today().year, today().month - offset
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _add(db, date_s, amount, desc="x", cat="Other"):
    db["expenses"].insert_one({"date": date_s, "amount": amount,
                               "description": desc, "category": cat})


def test_forecast_projects_from_pace(db):
    cur = _month(0)
    _add(db, f"{cur}-01", 1000)
    _add(db, f"{cur}-02", 500)
    f = E.spending_forecast()
    assert f["spent"] == 1500
    assert f["is_current"] is True
    assert f["projected"] >= f["spent"]           # extrapolated to month end
    assert f["days_in_month"] in (28, 29, 30, 31)


def test_forecast_compares_to_last_month(db):
    _add(db, f"{_month(1)}-10", 8000)
    _add(db, f"{_month(0)}-01", 1000)
    f = E.spending_forecast()
    assert f["last_month"] == 8000
    assert f["delta_vs_last"] == f["projected"] - 8000


def test_recurring_detected_across_months(db):
    for off in (0, 1, 2):
        _add(db, f"{_month(off)}-05", 199, desc="Netflix", cat="Entertainment")
    rec = E.detect_recurring()
    netflix = next((r for r in rec if r["description"] == "Netflix"), None)
    assert netflix is not None
    assert netflix["monthly"] == 199 and netflix["months"] == 3


def test_one_off_not_flagged_recurring(db):
    _add(db, f"{_month(0)}-05", 3000, desc="new headphones", cat="Shopping")
    assert all(r["description"] != "new headphones" for r in E.detect_recurring())


def test_endpoint(client, db):
    for off in (0, 1):
        _add(db, f"{_month(off)}-05", 500, desc="Gym", cat="Health")
    r = client.get("/money_insights").get_json()
    assert "forecast" in r and "recurring" in r
    assert r["recurring_monthly_total"] >= 500
