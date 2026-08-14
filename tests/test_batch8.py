"""Session edit/delete, backup download, protein suggestions, auto-deload
cancel, and the deload-week vs plateau factor fix."""

import agent_core


def _seed(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": "2026-08-10", "duration_min": 40, "body_weight_kg": 97,
         "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 18, "reps_done": 10,
             "sets": [{"weight": 18, "reps": 10}]},
            {"name": "Dumbbell Lateral Raise", "weight": 9, "reps_done": 14}]}]}


def test_update_session_edits_top_set(client, db, profile_doc):
    _seed(db, profile_doc)
    r = client.post("/update_session", json={
        "date": "2026-08-10", "day": "A", "duration_min": 45,
        "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 20.5, "reps": 8},   # changed
            {"name": "Dumbbell Lateral Raise", "weight": 9, "reps": 14}]})       # unchanged
    assert r.status_code == 200
    s = db["workout_log"].docs["log"]["sessions"][0]
    bench = next(e for e in s["exercises"] if e["name"] == "Dumbbell Flat Bench Press")
    assert bench["weight"] == 20.5 and bench["reps_done"] == 8
    assert "sets" not in bench          # per-set detail dropped since top set changed
    assert s["duration_min"] == 45


def test_update_session_keeps_sets_when_unchanged(client, db, profile_doc):
    _seed(db, profile_doc)
    client.post("/update_session", json={
        "date": "2026-08-10", "day": "A",
        "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 18, "reps": 10},     # unchanged
            {"name": "Dumbbell Lateral Raise", "weight": 10, "reps": 12}]})
    s = db["workout_log"].docs["log"]["sessions"][0]
    bench = next(e for e in s["exercises"] if e["name"] == "Dumbbell Flat Bench Press")
    assert bench.get("sets") == [{"weight": 18, "reps": 10}]   # preserved


def test_update_session_drops_blanked_exercise(client, db, profile_doc):
    _seed(db, profile_doc)
    client.post("/update_session", json={
        "date": "2026-08-10", "day": "A",
        "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 18, "reps": 10},
            {"name": "Dumbbell Lateral Raise", "weight": 0, "reps": 0}]})     # blanked
    s = db["workout_log"].docs["log"]["sessions"][0]
    assert len(s["exercises"]) == 1


def test_update_session_rejects_empty(client, db, profile_doc):
    _seed(db, profile_doc)
    r = client.post("/update_session", json={
        "date": "2026-08-10", "day": "A",
        "exercises": [{"name": "X", "weight": 0, "reps": 0}]})
    assert r.status_code == 400


def test_update_session_not_found(client, db, profile_doc):
    _seed(db, profile_doc)
    r = client.post("/update_session", json={
        "date": "2020-01-01", "day": "A",
        "exercises": [{"name": "X", "weight": 10, "reps": 10}]})
    assert r.status_code == 404


def test_delete_session(client, db, profile_doc):
    _seed(db, profile_doc)
    r = client.post("/delete_session", json={"date": "2026-08-10", "day": "A"})
    assert r.status_code == 200
    assert db["workout_log"].docs["log"]["sessions"] == []


def test_backup_download(client, db, profile_doc):
    _seed(db, profile_doc)
    r = client.get("/backup_download")
    assert r.status_code == 200 and "application/json" in r.content_type
    assert "attachment" in r.headers.get("Content-Disposition", "")
    assert b"workout_log" in r.data


def test_clear_autodeload(client, db, profile_doc):
    _seed(db, profile_doc)
    from progression import get_autodeload_flags, set_autodeload_flags
    set_autodeload_flags(["Dumbbell Flat Bench Press"])
    assert "Dumbbell Flat Bench Press" in get_autodeload_flags()
    r = client.post("/clear_autodeload", json={"name": "Dumbbell Flat Bench Press"})
    assert r.status_code == 200
    assert get_autodeload_flags() == []


def test_protein_fix_suggested_when_short(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)      # target ~194g protein
    db["meals"].rows = [{"_id": "m1", "date": agent_core.today_iso(),
                         "description": "toast", "calories": 200, "protein_g": 10}]
    d = client.get("/nutrition_data").get_json()
    assert d["protein_fix"] is not None
    assert d["protein_fix"]["gap"] > 100
    assert len(d["protein_fix"]["options"]) >= 1


def test_protein_fix_absent_when_met(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["meals"].rows = [{"_id": "m1", "date": agent_core.today_iso(),
                         "description": "big day", "calories": 2000, "protein_g": 300}]
    d = client.get("/nutrition_data").get_json()
    assert d["protein_fix"] is None


def test_deload_week_lighter_than_plateau(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    # exactly 24 sessions -> deload week; last-dated session is Day F so today
    # rotates back to Day A. The most recent Day-A session (bench 20.5x12) must
    # come last among Day-A entries so get_last_session_for_day finds it.
    sessions = [{"day": "A", "date": f"2026-06-{(i % 28) + 1:02d}", "exercises": []}
                for i in range(22)]
    sessions.append({"day": "A", "date": "2026-07-15", "exercises": [
        {"name": "Dumbbell Flat Bench Press", "weight": 20.5, "reps_done": 12}]})
    sessions.append({"day": "F", "date": "2026-08-10", "exercises": [
        {"name": "x", "weight": 10, "reps_done": 10}]})
    assert len(sessions) == 24
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": sessions}
    d = client.get("/today_program").get_json()
    assert d["deload"] is True and d["day"] == "A"
    bench = next(e for e in d["exercises"] if e["name"] == "Dumbbell Flat Bench Press")
    assert bench["suggestion"]["kind"] == "deload"
    assert bench["suggestion"]["weight"] <= 13.5      # ~60% of 20.5, not ~90%
