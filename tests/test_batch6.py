"""Tests for the backlog batch: default habit seeding, structured weight_log
storage, and apply_memory_update's dedup safety with unhashable items."""

import agent_core
import write_tools


def test_seed_default_habit_is_idempotent(db):
    agent_core.seed_default_habit()
    assert db["habits"].docs["Log meals"]["created"] == agent_core.today_iso()
    # Re-seeding (e.g. after a profile reset + re-onboard) must not clobber it
    db["habits"].docs["Log meals"]["created"] = "2020-01-01"
    agent_core.seed_default_habit()
    assert db["habits"].docs["Log meals"]["created"] == "2020-01-01"


def test_onboarding_save_seeds_the_habit(db, monkeypatch):
    import messaging
    monkeypatch.setattr(messaging, "load_profile", lambda: None)   # is_setup=False
    monkeypatch.setattr(messaging, "chat", lambda *a, **k:
        '<SAVE_PROFILE>{"name":"K","age":28,"weight_kg":97,"height_cm":178,'
        '"goal":"lose fat","level":"intermediate","days_per_week":6,'
        '"diet":"veg","session_min":"45","activity_level":"sedentary"}'
        '</SAVE_PROFILE>\nWelcome!')
    messaging.ask_agent([{"role": "user", "content": "hi"}], source="web")
    assert "Log meals" in db["habits"].docs


def test_log_body_weight_writes_structured_entry(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    tools = write_tools.make_write_tools({})
    tools["log_body_weight"](96.5)
    raw = db["memory"].docs["mem"]["weight_log"][0]
    assert raw == {"date": agent_core.today_iso(), "kg": 96.5}
    assert agent_core.get_weight_entries(db["memory"].docs["mem"]) == \
        [(agent_core.today_iso(), 96.5)]


def test_log_workout_session_weight_writes_structured_entry(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    tools = write_tools.make_write_tools({})
    tools["log_workout_session"](
        exercises=[{"name": "Goblet Squat", "weight": 20, "reps_done": 10}],
        body_weight_kg=96.5)
    raw = db["memory"].docs["mem"]["weight_log"][0]
    assert raw == {"date": agent_core.today_iso(), "kg": 96.5}


def test_apply_memory_update_dedupes_dict_and_string_items():
    mem = {"weight_log": ["2026-08-01: 97.0 kg"]}
    # Legacy string entries and new dict entries must coexist without
    # crashing (dicts are unhashable, the old set()-based dedup would raise).
    agent_core.apply_memory_update(mem, {"weight_log": [
        {"date": "2026-08-05", "kg": 96.5}]})
    assert len(mem["weight_log"]) == 2
    # Re-adding the exact same dict entry must not duplicate it
    agent_core.apply_memory_update(mem, {"weight_log": [
        {"date": "2026-08-05", "kg": 96.5}]})
    assert len(mem["weight_log"]) == 2
    entries = agent_core.get_weight_entries(mem)
    assert entries == [("2026-08-01", 97.0), ("2026-08-05", 96.5)]


def test_load_memory_fallback_does_not_share_mutable_defaults(db):
    """dict(DEFAULT_MEMORY) is a shallow copy — its list VALUES are the same
    objects as the module-level default, so mutating one poisons every future
    call that hits the no-doc-yet fallback (e.g. after the memory doc is
    deleted while the process keeps running). Regression for that bug."""
    mem1 = agent_core.load_memory()
    mem1["weight_log"].append({"date": "2020-01-01", "kg": 999})
    mem1["preferences"].append("should not leak")

    mem2 = agent_core.load_memory()   # still no doc saved -> same fallback path
    assert mem2["weight_log"] == []
    assert mem2["preferences"] == []
    assert agent_core.DEFAULT_MEMORY["weight_log"] == []


def test_mixed_format_weight_log_survives_undo(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["memory"].docs["mem"] = {"_id": "mem", "weight_log": ["2026-08-01: 97.0 kg"]}
    tools = write_tools.make_write_tools({})
    tools["log_body_weight"](96.5)
    assert len(db["memory"].docs["mem"]["weight_log"]) == 2
    import trust
    trust.undo_last()
    # Only the new (dict) entry is removed; the legacy string entry survives
    assert db["memory"].docs["mem"]["weight_log"] == ["2026-08-01: 97.0 kg"]
