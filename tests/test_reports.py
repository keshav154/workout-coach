"""Weekly calorie auto-adjust rules."""

from datetime import timedelta

import agent_core
import reports


def seed(db, profile_doc, goal, rate_per_week, cal_adjust=0, entries=5):
    p = dict(profile_doc)
    p["goal"] = goal
    if cal_adjust:
        p["cal_adjust"] = cal_adjust
    db["profile"].docs["user"] = p
    today = agent_core.today()
    w = 97.0
    log = []
    for i in range(entries):
        d = (today - timedelta(days=7 * (entries - 1 - i))).isoformat()
        log.append(f"{d}: {w:.1f} kg")
        w += rate_per_week
    db["memory"].docs["mem"] = {"_id": "mem", "weight_log": log}


def test_cut_flat_trend_reduces_calories(db, profile_doc):
    seed(db, profile_doc, "lose fat", 0.0)
    msg = reports.auto_adjust_calories()
    assert msg and "down by 200" in msg
    assert db["profile"].docs["user"]["cal_adjust"] == -200


def test_cut_on_pace_no_change(db, profile_doc):
    seed(db, profile_doc, "lose fat", -0.4)
    assert reports.auto_adjust_calories() is None


def test_cut_losing_too_fast_adds_calories(db, profile_doc):
    seed(db, profile_doc, "lose fat", -1.2)
    msg = reports.auto_adjust_calories()
    assert msg and "up by 200" in msg


def test_bulk_not_gaining_adds_calories(db, profile_doc):
    seed(db, profile_doc, "build muscle", 0.0)
    msg = reports.auto_adjust_calories()
    assert msg and "up by 200" in msg


def test_clamp_stops_runaway_adjustment(db, profile_doc):
    seed(db, profile_doc, "lose fat", 0.0, cal_adjust=-600)
    assert reports.auto_adjust_calories() is None


def test_insufficient_data_no_change(db, profile_doc):
    seed(db, profile_doc, "lose fat", 0.0, entries=2)
    assert reports.auto_adjust_calories() is None
