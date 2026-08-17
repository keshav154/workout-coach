"""AI-driven, equipment/injury-aware exercise swaps (LLM mocked)."""

import progression


def test_ai_swap_parses_dedupes_and_excludes_original(db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k:
        'here: ["Push-Ups", "push-ups", "Goblet Squat", "Goblet Squat"]')
    out = progression.ai_swap_alternatives("Goblet Squat")
    assert out == ["Push-Ups"]                     # dupes + the original removed


def test_ai_swap_falls_back_to_static_on_bad_json(db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "no json array here")
    out = progression.ai_swap_alternatives("Dumbbell Flat Bench Press")
    assert out == progression.alternatives_for("Dumbbell Flat Bench Press")
    assert "Push-Ups" in out


def test_ai_swap_falls_back_when_llm_down(db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    import llm
    def boom(*a, **k): raise RuntimeError("provider down")
    monkeypatch.setattr(llm, "chat", boom)
    out = progression.ai_swap_alternatives("Dumbbell Flat Bench Press")
    assert out == progression.alternatives_for("Dumbbell Flat Bench Press")


def test_ai_swap_works_for_unknown_exercise(db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k:
        '["Dumbbell Curl", "Hammer Curl", "Concentration Curl"]')
    # Not in the static table at all — AI still returns usable swaps.
    out = progression.ai_swap_alternatives("Some Custom Biceps Move")
    assert len(out) == 3 and "Dumbbell Curl" in out


def test_swap_endpoint(client, db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: '["Push-Ups", "Dip"]')
    r = client.get("/swap_alternatives?name=Dumbbell%20Flat%20Bench%20Press").get_json()
    assert r["alternatives"] == ["Push-Ups", "Dip"]
    assert client.get("/swap_alternatives").status_code == 400
