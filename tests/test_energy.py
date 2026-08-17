"""Adaptive TDEE: energy-balance estimate, guardrails, and target wiring."""

from datetime import timedelta

import energy
from agent_core import today, compute_targets


def _seed_profile(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)


def _seed_weight(db, points):
    """points = [(days_ago, kg), ...] written into memory.weight_log."""
    log = [{"date": (today() - timedelta(days=d)).isoformat(), "kg": kg}
           for d, kg in points]
    db["memory"].docs["mem"] = {"_id": "mem", "weight_log": log}


def _seed_meals(db, start_days_ago, n_days, kcal):
    for i in range(n_days):
        d = (today() - timedelta(days=start_days_ago - i)).isoformat()
        db["meals"].insert_one({"date": d, "description": "meal", "calories": kcal})


def test_estimate_needs_enough_signal(db, profile_doc):
    _seed_profile(db, profile_doc)
    _seed_weight(db, [(20, 97), (0, 97)])          # only 2 weigh-ins
    assert "error" in energy.estimate_maintenance()


def test_estimate_recovers_maintenance_when_losing(db, profile_doc):
    _seed_profile(db, profile_doc)
    # Lost 1 kg over 28 days while eating 2000 kcal/day.
    _seed_weight(db, [(28, 98), (14, 97.5), (0, 97)])
    _seed_meals(db, 28, 29, 2000)
    est = energy.estimate_maintenance()
    assert "error" not in est
    # deficit ~= 1kg*7700/28 ~= 275/day, so maintenance ~= 2275
    assert 2200 <= est["estimate"] <= 2350
    assert est["rate_kg_week"] < 0


def test_estimate_clamped_to_sane_band(db, profile_doc):
    _seed_profile(db, profile_doc)
    # Absurd: lost 12 kg in 28 days on 2000 kcal -> raw estimate way too high.
    _seed_weight(db, [(28, 109), (14, 103), (0, 97)])
    _seed_meals(db, 28, 29, 2000)
    est = energy.estimate_maintenance()
    assert est["clamped"] is True
    assert est["estimate"] <= 4500
    assert est["raw_estimate"] > est["estimate"]


def test_update_writes_learned_value_and_targets_follow(db, profile_doc):
    _seed_profile(db, profile_doc)
    _seed_weight(db, [(28, 98), (14, 97.5), (0, 97)])
    _seed_meals(db, 28, 29, 2000)
    msg = energy.update_maintenance()
    assert msg and "maintenance" in msg.lower()
    m = energy.get_learned_maintenance()
    assert m is not None
    # compute_targets should now report the measured source, not formula.
    t = compute_targets(db["profile"].docs["user"])
    assert t["tdee_source"] == "measured"
    assert t["tdee"] == m


def test_learned_value_goes_stale(db, profile_doc):
    _seed_profile(db, profile_doc)
    db["learned_params"].docs["maintenance_kcal"] = {
        "_id": "maintenance_kcal", "value": 2300,
        "updated": (today() - timedelta(days=40)).isoformat()}
    assert energy.get_learned_maintenance() is None      # too old to trust
    assert compute_targets(profile_doc)["tdee_source"] == "formula"


def test_update_zeroes_cal_adjust(db, profile_doc):
    p = dict(profile_doc); p["cal_adjust"] = 200
    db["profile"].docs["user"] = p
    _seed_weight(db, [(28, 98), (14, 97.5), (0, 97)])
    _seed_meals(db, 28, 29, 2000)
    energy.update_maintenance()
    assert db["profile"].docs["user"].get("cal_adjust") == 0
