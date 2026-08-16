"""Money history endpoint, expense delete, and RPE + superset logging."""



def test_money_data_history_and_trend(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["expenses"].rows = [
        {"_id": "e1", "date": "2026-08-03", "amount": 500, "category": "Food", "description": "dal"},
        {"_id": "e2", "date": "2026-08-03", "amount": 200, "category": "Transport", "description": "auto"},
        {"_id": "e3", "date": "2026-07-15", "amount": 1000, "category": "Bills", "description": "wifi"},
    ]
    d = client.get("/money_data?month=2026-08").get_json()
    assert d["month"] == "2026-08" and d["total"] == 700
    assert len(d["day_series"]) == 31            # August has 31 days
    aug3 = next(x for x in d["day_series"] if x["date"] == "2026-08-03")
    assert aug3["amount"] == 700
    assert len(d["months"]) == 6
    jul = next(m for m in d["months"] if m["month"] == "2026-07")
    assert jul["total"] == 1000
    # browse to July
    d2 = client.get("/money_data?month=2026-07").get_json()
    assert d2["total"] == 1000 and d2["is_current"] is False


def test_money_data_bad_month_falls_back(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    d = client.get("/money_data?month=garbage").get_json()
    assert len(d["month"]) == 7 and d["month"][4] == "-"


def test_delete_expense(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["expenses"].rows = [{"_id": "e1", "date": "2026-08-03", "amount": 500,
                            "category": "Food", "description": "dal"}]
    assert client.post("/delete_expense", json={"id": "e1"}).status_code == 200
    assert db["expenses"].rows == []
    assert client.post("/delete_expense", json={}).status_code == 400


def test_log_workout_stores_rpe_and_superset(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    r = client.post("/log_workout", json={"day": "A", "exercises": [
        {"name": "Bench", "sets": [{"weight": 18, "reps": 10, "rpe": 8}]},
        {"name": "Row", "superset": "A", "sets": [{"weight": 16, "reps": 12, "rpe": 9.5}]},
    ]})
    assert r.status_code == 200
    exs = db["workout_log"].docs["log"]["sessions"][-1]["exercises"]
    bench = next(e for e in exs if e["name"] == "Bench")
    row = next(e for e in exs if e["name"] == "Row")
    assert bench["sets"][0]["rpe"] == 8 and bench["rpe"] == 8
    assert row["superset"] == "A" and row["sets"][0]["rpe"] == 9.5
    # surfaced in stats detail
    detail = client.get("/stats").get_json()["recent_sessions"][0]["detail"]
    assert any(x.get("rpe") == 8 for x in detail)
    assert any(x.get("superset") == "A" for x in detail)


def test_log_workout_rejects_bad_rpe(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    client.post("/log_workout", json={"day": "A", "exercises": [
        {"name": "Bench", "sets": [{"weight": 18, "reps": 10, "rpe": 99}]}]})
    st = db["workout_log"].docs["log"]["sessions"][-1]["exercises"][0]["sets"][0]
    assert "rpe" not in st                        # 99 is out of range, dropped
