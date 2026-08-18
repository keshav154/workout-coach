"""Outcome feedback loop: record, grade, and learn from the coach's own decisions."""

from datetime import timedelta

import feedback
from agent_core import today, load_memory


def _ago(days):
    return (today() - timedelta(days=days)).isoformat()


def test_record_is_deduped(db):
    assert feedback.record_intervention("deload", "Row", 24.0) is True
    assert feedback.record_intervention("deload", "Row", 25.0) is False   # already open
    assert len(db["interventions"].rows) == 1


def test_deload_graded_improved_writes_observation(db):
    db["interventions"].insert_one({
        "kind": "deload", "key": "Row", "baseline": 24.0,
        "date": _ago(15), "evaluated": False, "outcome": None, "meta": {}})
    # A stronger session AFTER the deload -> plateau cleared (20kg x 10 -> e1RM ~26.7)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": _ago(3),
         "exercises": [{"name": "Row", "weight": 20, "reps_done": 10}]}]}
    report = feedback.evaluate_interventions()
    assert report and "1/1" in report
    doc = db["interventions"].rows[0]
    assert doc["evaluated"] is True and doc["outcome"] == "improved"
    obs = load_memory().get("coach_observations", [])
    assert any("deload" in o.lower() for o in obs)


def test_deload_graded_worse(db):
    db["interventions"].insert_one({
        "kind": "deload", "key": "Row", "baseline": 30.0,
        "date": _ago(15), "evaluated": False, "outcome": None, "meta": {}})
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": _ago(2),
         "exercises": [{"name": "Row", "weight": 18, "reps_done": 8}]}]}   # weaker
    feedback.evaluate_interventions()
    assert db["interventions"].rows[0]["outcome"] == "worse"


def test_not_due_is_left_pending(db):
    db["interventions"].insert_one({
        "kind": "deload", "key": "Row", "baseline": 24.0,
        "date": _ago(3), "evaluated": False, "outcome": None, "meta": {}})   # < 14d
    assert feedback.evaluate_interventions() is None
    assert db["interventions"].rows[0]["evaluated"] is False


def test_calorie_graded_toward_goal(db):
    db["memory"].docs["mem"] = {"_id": "mem", "weight_log": [
        {"date": _ago(1), "kg": 96.0}]}          # dropped from baseline 97
    db["interventions"].insert_one({
        "kind": "calorie", "key": "cal_adjust",
        "baseline": {"weight": 97.0, "goal": "lose fat"},
        "date": _ago(22), "evaluated": False, "outcome": None, "meta": {}})
    feedback.evaluate_interventions()
    assert db["interventions"].rows[0]["outcome"] == "improved"


def test_summary_reflects_graded(db):
    db["interventions"].insert_one({
        "kind": "deload", "key": "Row", "baseline": 24.0, "date": _ago(20),
        "evaluated": True, "outcome": "improved", "evaluated_date": _ago(1), "meta": {}})
    s = feedback.intervention_summary()
    assert "worked 1/1" in s
