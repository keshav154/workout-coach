"""Plateau detection (rep/volume-aware) and double-progression suggestions."""

from progression import detect_plateaus, suggest_next


def _sess(date, name, weight, reps, sets=None):
    ex = {"name": name, "weight": weight, "reps_done": reps}
    if sets is not None:
        ex["sets"] = sets
    return {"day": "A", "date": date, "exercises": [ex]}


def test_no_plateau_when_reps_increase_at_same_weight():
    # 18kg for 10, then 11, then 12 reps — weight flat but clear progress.
    log = {"sessions": [
        _sess("2026-08-01", "Bench", 18, 10),
        _sess("2026-08-08", "Bench", 18, 11),
        _sess("2026-08-15", "Bench", 18, 12),
    ]}
    assert detect_plateaus(log) == []


def test_no_plateau_when_sets_increase():
    # Same top set (18x10) but volume grows via added sets across sessions.
    log = {"sessions": [
        _sess("2026-08-01", "Bench", 18, 10, sets=[{"weight": 18, "reps": 10}]),
        _sess("2026-08-08", "Bench", 18, 10, sets=[{"weight": 18, "reps": 10},
                                                    {"weight": 18, "reps": 10}]),
        _sess("2026-08-15", "Bench", 18, 10, sets=[{"weight": 18, "reps": 10},
                                                    {"weight": 18, "reps": 10},
                                                    {"weight": 18, "reps": 10}]),
    ]}
    assert detect_plateaus(log) == []


def test_plateau_when_truly_stuck():
    # Identical weight, reps and volume for 3 sessions -> a real plateau.
    log = {"sessions": [
        _sess("2026-08-01", "Bench", 18, 10),
        _sess("2026-08-08", "Bench", 18, 10),
        _sess("2026-08-15", "Bench", 18, 10),
    ]}
    p = detect_plateaus(log)
    assert len(p) == 1 and "Bench" in p[0]


def test_plateau_needs_enough_sessions():
    log = {"sessions": [
        _sess("2026-08-01", "Bench", 18, 10),
        _sess("2026-08-08", "Bench", 18, 10),
    ]}
    assert detect_plateaus(log) == []


def test_suggest_rep_progression_below_top_of_range():
    s = suggest_next("8-12", last_weight=18, last_reps=10)
    assert s["kind"] == "rep_up" and s["weight"] == 18 and s["target_reps"] == 11


def test_suggest_weight_up_at_top_of_range():
    s = suggest_next("8-12", last_weight=18, last_reps=12)
    assert s["kind"] == "weight_up" and s["weight"] == 20.5 and s["target_reps"] == 8


def test_suggest_at_max_dumbbell():
    s = suggest_next("8-12", last_weight=24, last_reps=12)
    assert s["kind"] == "max" and s["weight"] == 24


def test_suggest_deload_week_vs_plateau_factor():
    # Deload WEEK ~60% of normal (much lighter than a plateau's ~10% touch).
    week = suggest_next("8-12", last_weight=20.5, last_reps=12, deload_factor=0.6)
    assert week["kind"] == "deload" and week["weight"] <= 13.5   # ~60% of 20.5
    plateau = suggest_next("8-12", last_weight=20.5, last_reps=12, deload_factor=0.9)
    assert plateau["kind"] == "deload" and plateau["weight"] == 18   # ~10% down
    assert week["weight"] < plateau["weight"]


def test_suggest_none_without_history():
    assert suggest_next("8-12", last_weight=0, last_reps=0) is None


def test_suggest_handles_each_side_rep_range():
    # "10 each" -> bounds (10, 10); 10 reps hits the top -> weight up
    s = suggest_next("10 each", last_weight=16, last_reps=10)
    assert s["kind"] == "weight_up" and s["weight"] == 18


def test_today_program_autofills_suggestion(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    # Prior Day-A bench 18kg x 12 (top of 8-12); a later Day-F session makes
    # today's rotation come back around to Day A.
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": "2026-08-01", "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 18, "reps_done": 12}]},
        {"day": "F", "date": "2026-08-10", "exercises": []}]}
    d = client.get("/today_program").get_json()
    assert d["day"] == "A"
    bench = next(e for e in d["exercises"] if e["name"] == "Dumbbell Flat Bench Press")
    assert bench["suggestion"]["kind"] == "weight_up"
    assert bench["suggestion"]["weight"] == 20.5
