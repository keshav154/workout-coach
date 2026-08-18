"""Proactive meal planning to close the remaining macro gap (LLM mocked)."""

import nutrition


def test_plans_options_for_remaining_budget(db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k:
        '[{"title":"Paneer plate","items":"paneer bhurji + 2 roti + curd",'
        '"calories":650,"protein_g":42,"carbs_g":55,"fat_g":22},'
        '{"title":"Rajma rice","items":"rajma + rice + whey","calories":600,'
        '"protein_g":45,"carbs_g":70,"fat_g":10}]')
    out = nutrition.plan_remaining_meals()
    assert "options" in out and len(out["options"]) == 2
    assert out["options"][0]["protein_g"] == 42
    assert out["remaining"]["calories"] > 0


def test_message_when_target_met(db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    # Log a huge meal so nothing meaningful remains.
    nutrition.log_meal("feast", calories=5000, protein=300)
    out = nutrition.plan_remaining_meals()
    assert "options" not in out and "message" in out


def test_bad_llm_returns_message_not_crash(db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "sorry no json")
    out = nutrition.plan_remaining_meals()
    assert "options" not in out and "message" in out
    assert "remaining" in out


def test_endpoint(client, db, profile_doc, monkeypatch):
    db["profile"].docs["user"] = dict(profile_doc)
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k:
        '[{"title":"A","items":"x","calories":500,"protein_g":40,"carbs_g":50,"fat_g":15},'
        '{"title":"B","items":"y","calories":400,"protein_g":35,"carbs_g":40,"fat_g":12}]')
    r = client.get("/meal_plan").get_json()
    assert len(r["options"]) == 2 and r["options"][0]["title"] == "A"
