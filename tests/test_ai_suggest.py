"""On-demand AI exercise suggestion (LLM mocked)."""

import progression


def _seed(db, sets_hist=None):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": "2026-08-01", "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 18, "reps_done": 10}]},
        {"day": "A", "date": "2026-08-08", "exercises": [
            {"name": "Dumbbell Flat Bench Press", "weight": 18, "reps_done": 12, "rpe": 7}]},
    ]}


def test_ai_suggest_parses_and_snaps_weight(db, monkeypatch):
    _seed(db)
    import llm
    monkeypatch.setattr(llm, "chat",
        lambda *a, **k: 'Sure! {"weight": 20, "target_reps": 8, "reason": "hit 12 reps, move up"} done')
    r = progression.ai_suggest_exercise("Dumbbell Flat Bench Press", "8-12")
    assert r["kind"] == "ai" and r["target_reps"] == 8
    assert r["weight"] == 20.5          # 20 snapped to nearest owned dumbbell
    assert "move up" in r["reason"]


def test_ai_suggest_no_history(db, monkeypatch):
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": []}
    r = progression.ai_suggest_exercise("Bench", "8-12")
    assert "error" in r and "history" in r["error"].lower()


def test_ai_suggest_bad_json(db, monkeypatch):
    _seed(db)
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "no json here")
    r = progression.ai_suggest_exercise("Dumbbell Flat Bench Press", "8-12")
    assert "error" in r


def test_ai_suggest_llm_down(db, monkeypatch):
    _seed(db)
    import llm
    def boom(*a, **k): raise RuntimeError("provider down")
    monkeypatch.setattr(llm, "chat", boom)
    r = progression.ai_suggest_exercise("Dumbbell Flat Bench Press", "8-12")
    assert "error" in r and "unavailable" in r["error"].lower()


def test_suggest_exercise_endpoint(client, db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    _seed(db)
    import llm
    monkeypatch.setattr(llm, "chat",
        lambda *a, **k: '{"weight": 20, "target_reps": 8, "reason": "progress"}')
    r = client.get("/suggest_exercise?name=Dumbbell%20Flat%20Bench%20Press&rep_range=8-12").get_json()
    assert r["weight"] == 20.5 and r["kind"] == "ai"
    assert client.get("/suggest_exercise").status_code == 400
