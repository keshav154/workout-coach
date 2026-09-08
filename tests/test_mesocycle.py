"""Periodization: mesocycle week, phase, volume ramp, scheduled deload."""

from datetime import timedelta

import mesocycle
from agent_core import today


def _log_started(weeks_ago):
    """A log whose first session was `weeks_ago` weeks back."""
    d = (today() - timedelta(weeks=weeks_ago)).isoformat()
    return {"_id": "log", "sessions": [
        {"day": "A", "date": d, "exercises": [{"name": "Row", "weight": 18, "reps_done": 10}]}]}


def test_no_history_is_base_week(db):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    m = mesocycle.mesocycle_status()
    assert m["week"] == 1 and m["phase"] == "base" and m["extra_sets"] == 0


def test_accumulation_ramps_sets(db):
    db["workout_log"].docs["log"] = _log_started(2)      # week 3 of block
    m = mesocycle.mesocycle_status()
    assert m["week"] == 3 and m["phase"] == "accumulation"
    assert m["extra_sets"] == 2                           # week-1 ramp


def test_deload_on_last_week(db):
    db["workout_log"].docs["log"] = _log_started(4)      # default 5-week block -> week 5
    m = mesocycle.mesocycle_status()
    assert m["is_deload_week"] is True and m["phase"] == "deload"
    assert m["extra_sets"] == 0 and m["weeks_to_deload"] == 0


def test_cycle_wraps_after_block(db):
    db["workout_log"].docs["log"] = _log_started(5)      # week 6 -> wraps to week 1
    m = mesocycle.mesocycle_status()
    assert m["week"] == 1 and m["phase"] == "base"


def test_extra_sets_capped(db):
    import learned_params as lp
    lp.set_param("mesocycle_weeks", 8, "long block")
    db["workout_log"].docs["log"] = _log_started(6)      # week 7 of 8 -> ramp would be 6
    m = mesocycle.mesocycle_status()
    assert m["extra_sets"] == mesocycle.MAX_EXTRA_SETS   # capped at 3


def test_learned_block_length_respected(db):
    import learned_params as lp
    lp.set_param("mesocycle_weeks", 4, "shorter")
    db["workout_log"].docs["log"] = _log_started(3)      # week 4 of 4 -> deload
    assert mesocycle.mesocycle_status()["is_deload_week"] is True


def test_block_string_warns_before_deload(db):
    db["workout_log"].docs["log"] = _log_started(3)      # week 4 of 5 -> deload next
    block = mesocycle.format_mesocycle_block()
    assert "deload comes next week" in block


def test_endpoint(client, db):
    db["workout_log"].docs["log"] = _log_started(1)
    r = client.get("/mesocycle").get_json()
    assert r["week"] == 2 and "focus" in r
