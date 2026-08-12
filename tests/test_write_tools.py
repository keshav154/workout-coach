"""Write-tool behavior against the fake DB."""

import agent_core
import write_tools


def _tools(ctx=None):
    return write_tools.make_write_tools(ctx if ctx is not None else {})


def test_same_day_relog_merges_instead_of_advancing_rotation(db):
    today = agent_core.today_iso()
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": today, "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 18, "reps_done": 10}]}]}
    db["profile"].docs["user"] = {"_id": "user", "name": "K"}

    r = _tools()["log_workout_session"](exercises=[
        {"name": "Dumbbell Bicep Curl", "weight": 13.5, "reps_done": 12}])
    assert "updated" in r

    sessions = db["workout_log"].docs["log"]["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["day"] == "A"
    names = sorted(e["name"] for e in sessions[0]["exercises"])
    assert names == ["Dumbbell Bicep Curl", "Dumbbell Flat Bench Press"]
    assert agent_core.get_next_day(db["workout_log"].docs["log"]) == "B"


def test_fresh_day_logs_next_rotation_day(db):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    db["profile"].docs["user"] = {"_id": "user", "name": "K"}
    r = _tools()["log_workout_session"](exercises=[
        {"name": "Goblet Squat", "weight": 20, "reps_done": 10}])
    assert "logged" in r
    assert db["workout_log"].docs["log"]["sessions"][0]["day"] == "A"


def test_body_weight_validation(db):
    db["profile"].docs["user"] = {"_id": "user", "name": "K"}
    tools = _tools()
    assert "REJECTED" in tools["log_body_weight"](12)
    assert tools["log_body_weight"](97.3).startswith("SAVED")
    assert db["profile"].docs["user"]["weight_kg"] == 97.3


def test_measurement_validation_and_save(db):
    tools = _tools()
    assert "REJECTED" in tools["log_body_measurement"]("elbow", 90)
    assert "REJECTED" in tools["log_body_measurement"]("waist", 500)
    assert tools["log_body_measurement"]("waist", 92).startswith("SAVED")
    rows = db["measurements"].rows
    assert rows[0]["part"] == "waist" and rows[0]["cm"] == 92


def test_habit_lifecycle(db):
    tools = _tools()
    assert tools["add_habit"]("3L water").startswith("SAVED")
    # Fuzzy match on done
    assert tools["log_habit_done"]("water").startswith("SAVED")
    assert "Already" in tools["log_habit_done"]("3L water")
    assert "REJECTED" in tools["log_habit_done"]("meditation")
    assert tools["remove_habit"]("3L water").startswith("SAVED")
    assert "REJECTED" in tools["remove_habit"]("3L water")
