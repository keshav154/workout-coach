"""PR / next-milestone prediction from the e1RM trend."""

import progression as P


def _log(sessions):
    return {"sessions": sessions}


def _sess(date, name, weight, reps):
    return {"day": "A", "date": date,
            "exercises": [{"name": name, "weight": weight, "reps_done": reps}]}


def test_improving_lift_projects_a_milestone(db):
    # Steadily adding reps at 18kg -> rising e1RM, next bell is 20.5.
    log = _log([
        _sess("2026-08-01", "Dumbbell Flat Bench Press", 18, 8),
        _sess("2026-08-04", "Dumbbell Flat Bench Press", 18, 9),
        _sess("2026-08-07", "Dumbbell Flat Bench Press", 18, 10),
        _sess("2026-08-10", "Dumbbell Flat Bench Press", 18, 11),
    ])
    preds = {p["name"]: p for p in P.pr_predictions(log)}
    row = preds["Dumbbell Flat Bench Press"]
    assert row["trend"] == "improving"
    assert row["sessions_away"] is not None and row["sessions_away"] >= 1
    assert row["milestone"].startswith("20.5kg")


def test_stalled_lift_has_no_eta(db):
    log = _log([
        _sess("2026-08-01", "Row", 18, 10),
        _sess("2026-08-04", "Row", 18, 10),
        _sess("2026-08-07", "Row", 18, 10),
    ])
    preds = {p["name"]: p for p in P.pr_predictions(log)}
    assert preds["Row"]["sessions_away"] is None
    assert preds["Row"]["trend"] in ("stalled", "declining")


def test_too_little_history_is_skipped(db):
    log = _log([_sess("2026-08-01", "Row", 18, 10),
                _sess("2026-08-04", "Row", 18, 11)])
    assert P.pr_predictions(log) == []


def test_soonest_first_ordering(db):
    log = _log([
        _sess("2026-08-01", "Fast", 18, 8), _sess("2026-08-03", "Fast", 18, 11),
        _sess("2026-08-05", "Fast", 20.5, 8),
        _sess("2026-08-01", "Slow", 18, 8), _sess("2026-08-04", "Slow", 18, 8),
        _sess("2026-08-07", "Slow", 18, 9),
    ])
    preds = P.pr_predictions(log)
    etas = [p["sessions_away"] for p in preds if p["sessions_away"] is not None]
    assert etas == sorted(etas)          # ascending; None sinks below


def test_endpoint(client, db):
    db["workout_log"].docs["log"] = _log([
        _sess("2026-08-01", "Dumbbell Flat Bench Press", 18, 8),
        _sess("2026-08-04", "Dumbbell Flat Bench Press", 18, 10),
        _sess("2026-08-07", "Dumbbell Flat Bench Press", 18, 12),
    ])
    db["workout_log"].docs["log"]["_id"] = "log"
    r = client.get("/pr_predictions").get_json()
    assert "predictions" in r and len(r["predictions"]) >= 1
