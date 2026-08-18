"""Weekly sets-per-muscle volume + landmark classification."""

from datetime import timedelta

import muscles
from agent_core import today


def test_classifier_maps_common_lifts():
    assert muscles.muscle_groups("Dumbbell Flat Bench Press") == ["Chest"]
    assert muscles.muscle_groups("Dumbbell Bent-Over Row") == ["Back"]
    assert muscles.muscle_groups("Goblet Squat") == ["Legs"]
    assert muscles.muscle_groups("Dumbbell Lateral Raise") == ["Shoulders"]
    assert muscles.muscle_groups("Dumbbell Bicep Curl") == ["Biceps"]
    assert muscles.muscle_groups("Tricep Overhead Extension") == ["Triceps"]


def test_romanian_deadlift_is_legs_not_back():
    assert muscles.muscle_groups("Romanian Deadlift") == ["Legs"]


def test_unknown_lift_returns_empty():
    assert muscles.muscle_groups("Interpretive Dance") == []


def _week_log(db, exercises):
    d = today().isoformat()
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": d, "exercises": exercises}]}


def test_counts_sets_from_per_set_list(db):
    _week_log(db, [{"name": "Dumbbell Flat Bench Press",
                    "sets": [{"weight": 18, "reps": 10}, {"weight": 18, "reps": 9},
                             {"weight": 18, "reps": 8}]}])
    rows = {r["muscle"]: r for r in muscles.weekly_muscle_volume()}
    assert rows["Chest"]["sets"] == 3
    assert rows["Chest"]["status"] == "low"        # 3 < 10


def test_summary_log_falls_back_to_prescribed_sets(db):
    # No per-set list -> uses the program's prescribed set count (Bench = 4).
    _week_log(db, [{"name": "Dumbbell Flat Bench Press", "weight": 18, "reps_done": 10}])
    rows = {r["muscle"]: r for r in muscles.weekly_muscle_volume()}
    assert rows["Chest"]["sets"] == 4


def test_high_volume_flagged(db):
    _week_log(db, [{"name": "Dumbbell Lateral Raise",
                    "sets": [{"weight": 8, "reps": 15}] * 26}])
    rows = {r["muscle"]: r for r in muscles.weekly_muscle_volume()}
    assert rows["Shoulders"]["status"] == "high"   # 26 > 24


def test_last_week_offset_excludes_this_week(db):
    _week_log(db, [{"name": "Goblet Squat", "sets": [{"weight": 20, "reps": 10}]}])
    last = {r["muscle"]: r for r in muscles.weekly_muscle_volume(week_offset=1)}
    assert last["Legs"]["sets"] == 0               # nothing logged last week


def test_prompt_block_only_lists_when_flagged(db):
    _week_log(db, [{"name": "Dumbbell Flat Bench Press",
                    "sets": [{"weight": 18, "reps": 10}]}])
    block = muscles.format_muscle_volume_block()
    assert "WEEKLY MUSCLE VOLUME" in block and "Chest" in block
