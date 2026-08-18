"""Session autoregulation: ease targets to recovery readiness, never add load."""

import progression as P


def _sug(weight=20.5, reps=8, kind="weight_up"):
    return {"weight": weight, "target_reps": reps, "kind": kind, "reason": "x"}


def test_no_change_when_readiness_good(db):
    s = _sug()
    out = P.autoregulate(dict(s), readiness=8)
    assert out == s                       # fresh day untouched


def test_eases_weight_when_readiness_low(db):
    out = P.autoregulate(_sug(weight=20.5), readiness=4)
    assert out["kind"] == "autoreg"
    assert out["weight"] < 20.5
    assert out["weight"] in P.AVAILABLE_DUMBBELLS   # snapped to an owned dumbbell
    assert "readiness 4/10" in out["reason"]


def test_very_low_readiness_eases_more(db):
    mild = P.autoregulate(_sug(weight=24), readiness=4)["weight"]
    hard = P.autoregulate(_sug(weight=24), readiness=1)["weight"]
    assert hard <= mild < 24


def test_never_stacks_on_deload(db):
    s = _sug(kind="deload")
    assert P.autoregulate(dict(s), readiness=1) == s


def test_none_suggestion_and_none_readiness(db):
    assert P.autoregulate(None, readiness=2) is None
    assert P.autoregulate(_sug(), readiness=None)["kind"] == "weight_up"


def test_respects_learned_threshold(db):
    import learned_params as lp
    # Default threshold 4: readiness 5 is untouched.
    assert P.autoregulate(_sug(), readiness=5)["kind"] == "weight_up"
    lp.set_param("autoreg_threshold", 6, "eases on mediocre days too")
    assert P.autoregulate(_sug(), readiness=5)["kind"] == "autoreg"


def test_lightest_weight_cannot_go_lower(db):
    lightest = P.AVAILABLE_DUMBBELLS[0]
    out = P.autoregulate(_sug(weight=lightest), readiness=1)
    assert out["weight"] == lightest and out["kind"] == "weight_up"   # unchanged
