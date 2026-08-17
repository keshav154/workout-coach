"""Adaptive learned parameters: guardrails, wiring, and the reflection loop."""

import learned_params as lp


def test_default_when_unset(db):
    assert lp.get_param("plateau_lookback") == 3
    assert lp.get_param("deload_week_factor") == 0.6


def test_set_and_read_back(db):
    lp.set_param("plateau_lookback", 5, "user progresses slowly")
    assert lp.get_param("plateau_lookback") == 5
    rows = {p["key"]: p for p in lp.all_params()}
    assert rows["plateau_lookback"]["learned"] is True
    assert "slowly" in rows["plateau_lookback"]["reason"]


def test_value_is_clamped_to_guardrails(db):
    lp.set_param("deload_week_factor", 0.1, "too aggressive")   # min is 0.5
    assert lp.get_param("deload_week_factor") == 0.5
    lp.set_param("plateau_lookback", 999, "absurd")             # max is 6
    assert lp.get_param("plateau_lookback") == 6


def test_unknown_key_and_bad_value_rejected(db):
    assert lp.set_param("not_a_real_param", 3, "x") is None
    assert lp.set_param("rep_increment", "banana", "x") is None


def test_reset_reverts_to_default(db):
    lp.set_param("rep_increment", 3, "fast progresser")
    assert lp.get_param("rep_increment") == 3
    lp.reset_param("rep_increment")
    assert lp.get_param("rep_increment") == 1


def test_progression_reads_learned_rep_increment(db):
    import progression
    lp.set_param("rep_increment", 3, "fast")
    s = progression.suggest_next("8-12", last_weight=18, last_reps=9)
    assert s["kind"] == "rep_up" and s["target_reps"] == 12   # 9 + 3


def test_detect_plateaus_uses_learned_lookback(db):
    import progression
    # Two flat sessions: plateaued at lookback 2, not at lookback 3.
    log = {"sessions": [
        {"date": "2026-08-01", "exercises": [{"name": "Row", "weight": 18, "reps_done": 10}]},
        {"date": "2026-08-04", "exercises": [{"name": "Row", "weight": 18, "reps_done": 10}]},
    ]}
    assert progression.detect_plateaus(log) == []          # default lookback 3
    lp.set_param("plateau_lookback", 2, "flags sooner")
    assert any("Row" in p for p in progression.detect_plateaus(log))


def test_reflect_and_tune_applies_valid_proposal(db, monkeypatch):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": "2026-08-10", "exercises": [
            {"name": "Row", "weight": 18, "reps_done": 10}]}]}
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k:
        '{"plateau_lookback": {"value": 5, "reason": "slow but steady"}}')
    report = lp.reflect_and_tune()
    assert report and "plateau_lookback" in report
    assert lp.get_param("plateau_lookback") == 5


def test_reflect_and_tune_rejects_out_of_spec(db, monkeypatch):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    import llm
    # bogus key + a clampable value; bogus key ignored, real one clamped
    monkeypatch.setattr(llm, "chat", lambda *a, **k:
        '{"hack_key": {"value": 1, "reason": "x"}, "rep_increment": {"value": 99, "reason": "max"}}')
    lp.reflect_and_tune()
    assert lp.get_param("rep_increment") == 3      # clamped to max, not 99
    assert lp.get_param("plateau_lookback") == 3   # untouched default
