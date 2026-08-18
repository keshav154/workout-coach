"""Daily cross-domain briefing: generation, per-day caching, refresh."""

import insights


def _seed(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        {"day": "A", "date": "2026-08-10", "exercises": [
            {"name": "Row", "weight": 18, "reps_done": 10}]}]}


def test_generates_and_caches(db, profile_doc, monkeypatch):
    _seed(db, profile_doc)
    calls = {"n": 0}
    import llm
    def fake(*a, **k):
        calls["n"] += 1
        return "Sleep dipped and protein was low 3/7 days — that's the likely stall, not the program."
    monkeypatch.setattr(llm, "chat", fake)

    first = insights.generate_briefing()
    assert first["cached"] is False and "stall" in first["text"]
    second = insights.generate_briefing()
    assert second["cached"] is True
    assert calls["n"] == 1                     # cache hit -> no second LLM call


def test_force_regenerates(db, profile_doc, monkeypatch):
    _seed(db, profile_doc)
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "read one")
    insights.generate_briefing()
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "read two")
    out = insights.generate_briefing(force=True)
    assert out["cached"] is False and out["text"] == "read two"


def test_empty_when_profile_incomplete(db, monkeypatch):
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "should not be used")
    out = insights.generate_briefing()
    assert out["text"] == ""


def test_gather_signals_has_all_domains(db, profile_doc, monkeypatch):
    _seed(db, profile_doc)
    s = insights.gather_signals()
    for label in ("Training:", "Recovery:", "Nutrition:", "Body weight:", "Spending:"):
        assert label in s


def test_endpoint(client, db, profile_doc, monkeypatch):
    _seed(db, profile_doc)
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "your read for today")
    r = client.get("/briefing").get_json()
    assert r["text"] == "your read for today"
