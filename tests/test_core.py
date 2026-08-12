"""Pure-logic tests: no DB needed."""

from datetime import timedelta

import agent_core
from agent_core import (_num, detect_prs, effective_calorie_target,
                        get_consecutive_workout_days, get_next_day,
                        warmup_weight_for)


def test_num_parses_messy_values():
    assert _num("12") == 12
    assert _num("10-12") == 10
    assert _num("10 each") == 10
    assert _num("24kg") == 24
    assert _num(None) == 0
    assert _num("garbage") == 0


def test_warmup_weight_rounds_down_to_available_dumbbell():
    assert warmup_weight_for(24) == 11.5     # 55% = 13.2
    assert warmup_weight_for(18) == 9        # 55% = 9.9
    assert warmup_weight_for(9) == 4.5
    assert warmup_weight_for(8) is None      # too light to warm up for
    assert warmup_weight_for(None) is None
    assert warmup_weight_for("nope") is None


def test_get_next_day_uses_latest_date_not_array_order():
    log = {"sessions": [
        {"day": "C", "date": "2026-08-10"},
        {"day": "A", "date": "2026-08-01"},   # out of order on purpose
    ]}
    assert get_next_day(log) == "D"
    assert get_next_day({"sessions": []}) == "A"


def test_detect_prs_compares_against_history():
    log = {"sessions": [{"day": "A", "date": "2026-08-01", "exercises": [
        {"name": "Bench", "weight": 16, "reps_done": 12}]}]}
    new = {"exercises": [{"name": "Bench", "weight": 18, "reps_done": 10},
                         {"name": "Curl", "weight": 13.5, "reps_done": 10}]}
    prs = detect_prs(log, new)
    assert len(prs) == 1 and "Bench" in prs[0]      # first-ever Curl is not a PR
    # Same weight, fewer reps: no PR
    assert detect_prs(log, {"exercises": [
        {"name": "Bench", "weight": 16, "reps_done": 11}]}) == []


def test_effective_calorie_target_applies_and_clamps_adjustment():
    p = {"weight_kg": 97, "height_cm": 178, "age": 28, "goal": "lose fat",
         "activity_level": "sedentary"}
    base = agent_core.compute_targets(p)["calorie_target"]
    assert effective_calorie_target(p) == base
    p["cal_adjust"] = -200
    assert effective_calorie_target(p) == base - 200
    p["cal_adjust"] = -9999
    assert effective_calorie_target(p) == base - 600
    p["cal_adjust"] = "garbage"
    assert effective_calorie_target(p) == base


def test_consecutive_workout_days_streak():
    today = agent_core.today()
    log = {"sessions": [
        {"date": (today - timedelta(days=i)).isoformat()} for i in range(3)]}
    assert get_consecutive_workout_days(log) == 3
    # Gap yesterday breaks the streak
    log = {"sessions": [{"date": (today - timedelta(days=2)).isoformat()}]}
    assert get_consecutive_workout_days(log) == 0
