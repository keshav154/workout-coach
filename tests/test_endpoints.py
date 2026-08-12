"""Flask endpoint tests via the test client and fake DB."""

import agent_core


def seed_basics(db, profile_doc):
    today = agent_core.today_iso()
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "F", "date": "2026-08-08", "exercises": []},
        {"day": "A", "date": "2026-08-01", "duration_min": 48, "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 16, "reps_done": 12}]},
        {"day": "A", "date": "2026-08-07", "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 18, "reps_done": 10,
             "sets": [{"weight": 16, "reps": 12}, {"weight": 18, "reps": 10}]}]},
    ]}
    db["memory"].docs["mem"] = {"_id": "mem",
        "personal_records": ["p1", "p2", "p3", "p4", "p5"],
        "weight_log": ["2026-07-15: 98.0 kg", "2026-07-29: 97.2 kg",
                       "2026-08-10: 96.5 kg"]}
    return today


def test_today_program_warmups_history_and_week(client, db, profile_doc):
    seed_basics(db, profile_doc)
    d = client.get("/today_program").get_json()
    assert d["ready"] and d["day"] == "A"
    assert "treadmill" in d["warmup"]
    assert [x["day"] for x in d["rotation"]] == ["A", "B", "C", "D", "E", "F"]
    assert len(d["week"]) == 6 and d["week"][1]["focus"]
    bench = next(e for e in d["exercises"] if e["name"] == "Dumbbell Flat Bench Press")
    assert bench["warmup_weight"] == 9 and bench["last_weight"] == 18
    assert bench["history"][0]["date"] == "2026-08-07"
    assert bench["alternatives"]


def test_log_workout_stores_duration_and_rejects_bad(client, db, profile_doc):
    seed_basics(db, profile_doc)
    r = client.post("/log_workout", json={
        "day": "B", "duration_min": 52,
        "exercises": [{"name": "Row", "sets": [{"weight": 16, "reps": 10}]}]})
    assert r.status_code == 200
    assert db["workout_log"].docs["log"]["sessions"][-1]["duration_min"] == 52
    client.post("/log_workout", json={
        "day": "C", "duration_min": 9999,
        "exercises": [{"name": "Squat", "sets": [{"weight": 20, "reps": 10}]}]})
    assert "duration_min" not in db["workout_log"].docs["log"]["sessions"][-1]


def test_records(client, db, profile_doc):
    seed_basics(db, profile_doc)
    d = client.get("/records").get_json()
    assert d["best"][0]["name"] == "Dumbbell Flat Bench Press"
    assert d["best"][0]["weight"] == 18


def test_goals_data_progress(client, db, profile_doc):
    seed_basics(db, profile_doc)
    db["goals"].rows = [
        {"_id": "g1", "kind": "weight", "target": 92, "by_date": "2026-11-01",
         "created": "2026-07-10"},
        {"_id": "g2", "kind": "lift", "target": 24, "exercise": "bench press",
         "created": "2026-07-10"}]
    d = client.get("/goals_data").get_json()
    wg = next(g for g in d["goals"] if g["kind"] == "weight")
    lg = next(g for g in d["goals"] if g["kind"] == "lift")
    assert wg["pct"] == 25 and wg["current"] == 96.5   # 98 -> 96.5 toward 92
    assert lg["current"] == 18 and lg["pct"] == 75


def test_achievements(client, db, profile_doc):
    seed_basics(db, profile_doc)
    d = client.get("/achievements").get_json()
    by_label = {b["label"]: b for b in d["badges"]}
    assert by_label["10 sessions"]["achieved"] is False
    assert by_label["5 PRs"]["achieved"] is True


def test_measurements_endpoint(client, db, profile_doc):
    seed_basics(db, profile_doc)
    db["measurements"].rows = [
        {"_id": "m1", "date": "2026-08-01", "part": "waist", "cm": 93.0},
        {"_id": "m2", "date": "2026-08-10", "part": "waist", "cm": 92.0}]
    d = client.get("/measurements").get_json()
    assert len(d["series"]["waist"]) == 2
    assert d["latest"]["waist"]["cm"] == 92.0


def test_habits_toggle_flow(client, db, profile_doc):
    seed_basics(db, profile_doc)
    db["habits"].docs["3L water"] = {"_id": "3L water", "created": "2026-08-01"}
    d = client.get("/habits").get_json()
    assert d["habits"][0] == {"name": "3L water", "done_today": False, "streak": 0}
    d = client.post("/habit_toggle", json={"name": "3L water"}).get_json()
    assert d["habits"][0]["done_today"] is True and d["habits"][0]["streak"] == 1
    d = client.post("/habit_toggle", json={"name": "3L water"}).get_json()
    assert d["habits"][0]["done_today"] is False
    assert client.post("/habit_toggle", json={"name": "nope"}).status_code == 400


def test_photos_roundtrip(client, db, profile_doc):
    import base64
    seed_basics(db, profile_doc)
    b64 = base64.b64encode(b"fake-jpeg-bytes").decode()
    assert client.post("/photos", json={"b64": b64, "mime": "image/jpeg"}).status_code == 200
    assert client.post("/photos", json={}).status_code == 400
    assert client.post("/photos", json={"b64": b64, "mime": "text/html"}).status_code == 400
    metas = client.get("/photos").get_json()["photos"]
    assert len(metas) == 1
    r = client.get(f"/photo/{metas[0]['id']}")
    assert r.status_code == 200 and r.data == b"fake-jpeg-bytes"
    assert client.get("/photo/missing").status_code == 404


def test_export_csv(client, db, profile_doc):
    seed_basics(db, profile_doc)
    r = client.get("/export_csv?what=workouts")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "16x12;18x10" in body and "48" in body
    r = client.get("/export_csv?what=weight")
    assert "2026-08-10,96.5" in r.get_data(as_text=True)
    assert client.get("/export_csv?what=nope").status_code == 400


def test_nutrition_and_money(client, db, profile_doc):
    today = seed_basics(db, profile_doc)
    db["profile"].docs["user"]["cal_adjust"] = -200
    db["meals"].rows = [{"_id": "m1", "date": today, "description": "Dal + roti",
                         "calories": 400, "protein_g": 20, "note": ""}]
    db["expenses"].rows = [
        {"_id": "e1", "date": today, "amount": 500, "description": "groceries",
         "category": "Food", "note": ""}]
    db["budget"].docs["monthly"] = {"_id": "monthly", "Food": 8000}

    d = client.get("/nutrition_data").get_json()
    base = agent_core.compute_targets(db["profile"].docs["user"])["calorie_target"]
    assert d["totals"]["calories"] == 400
    assert d["targets"]["calories"] == base - 200
    assert len(d["week"]) == 7

    d = client.get("/money_data").get_json()
    assert d["total"] == 500
    assert d["by_category"][0]["budget"] == 8000
