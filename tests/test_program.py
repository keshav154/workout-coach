"""Custom program builder: storage, validation, and its effect on rotation."""

import agent_core


def test_default_program_when_none_saved(db):
    prog = agent_core.get_program()
    assert list(prog.keys()) == ["A", "B", "C", "D", "E", "F"]
    assert agent_core.get_rotation() == ["A", "B", "C", "D", "E", "F"]
    assert agent_core.program_is_custom() is False


def test_save_and_load_custom_program(db):
    ok, msg = agent_core.save_program([
        {"name": "Upper", "focus": "chest/back", "exercises": [
            {"name": "Bench", "sets": 4, "rep_range": "6-10"},
            {"name": "Row", "sets": 4, "rep_range": "8-12"}]},
        {"name": "Lower", "focus": "legs", "exercises": [
            {"name": "Squat", "sets": 5, "rep_range": "5"}]},
    ])
    assert ok, msg
    prog = agent_core.get_program()
    assert list(prog.keys()) == ["A", "B"]
    assert prog["A"]["name"] == "Upper"
    assert prog["A"]["exercises"][0] == {"name": "Bench", "sets": 4, "rep_range": "6-10"}
    assert agent_core.get_rotation() == ["A", "B"]
    assert agent_core.program_is_custom() is True


def test_program_rotation_drives_next_day(db):
    agent_core.save_program([
        {"name": "Upper", "exercises": [{"name": "Bench", "sets": 3, "rep_range": "8-12"}]},
        {"name": "Lower", "exercises": [{"name": "Squat", "sets": 3, "rep_range": "8-12"}]},
    ])
    # No sessions -> first day
    assert agent_core.get_next_day({"sessions": []}) == "A"
    # After Day A -> B; after B -> wraps to A (only 2 days)
    assert agent_core.get_next_day({"sessions": [{"day": "A", "date": "2026-08-10"}]}) == "B"
    assert agent_core.get_next_day({"sessions": [{"day": "B", "date": "2026-08-11"}]}) == "A"


def test_next_day_survives_removed_day(db):
    # Old session on Day F, but the new program only has A and B.
    agent_core.save_program([
        {"name": "Upper", "exercises": [{"name": "Bench", "sets": 3, "rep_range": "8-12"}]},
        {"name": "Lower", "exercises": [{"name": "Squat", "sets": 3, "rep_range": "8-12"}]},
    ])
    assert agent_core.get_next_day({"sessions": [{"day": "F", "date": "2026-08-10"}]}) == "A"


def test_reset_program(db):
    agent_core.save_program([
        {"name": "X", "exercises": [{"name": "Bench", "sets": 3, "rep_range": "8-12"}]}])
    assert agent_core.program_is_custom() is True
    agent_core.reset_program()
    assert agent_core.program_is_custom() is False
    assert list(agent_core.get_program().keys()) == ["A", "B", "C", "D", "E", "F"]


def test_save_validation(db):
    assert agent_core.save_program([])[0] is False               # no days
    assert agent_core.save_program([{"exercises": []}])[0] is False   # empty day
    ok, _ = agent_core.save_program([{"exercises": [{"name": "Bench"}]}])
    assert ok                                                    # defaults fill in
    prog = agent_core.get_program()
    assert prog["A"]["exercises"][0]["sets"] == 3 and prog["A"]["exercises"][0]["rep_range"] == "8-12"
    # too many days
    assert agent_core.save_program([{"exercises": [{"name": "x"}]}] * 8)[0] is False


def test_save_clamps_sets_and_drops_blank_names(db):
    agent_core.save_program([{"name": "D", "exercises": [
        {"name": "Bench", "sets": 99, "rep_range": "8-12"},
        {"name": "", "sets": 3, "rep_range": "8-12"}]}])
    exs = agent_core.get_program()["A"]["exercises"]
    assert len(exs) == 1 and exs[0]["sets"] == 10          # clamped, blank dropped


def test_program_endpoints(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    d = client.get("/program").get_json()
    assert len(d["days"]) == 6 and d["is_custom"] is False

    r = client.post("/program", json={"days": [
        {"name": "Full body", "exercises": [
            {"name": "Goblet Squat", "sets": 3, "rep_range": "10-12"}]}]})
    assert r.status_code == 200
    d = client.get("/program").get_json()
    assert len(d["days"]) == 1 and d["is_custom"] is True and d["days"][0]["id"] == "A"

    # today_program now reflects the custom program
    tp = client.get("/today_program").get_json()
    assert tp["day"] == "A" and tp["exercises"][0]["name"] == "Goblet Squat"
    assert [x["day"] for x in tp["rotation"]] == ["A"]

    assert client.post("/program", json={"days": []}).status_code == 400
    assert client.post("/program", json={"reset": True}).status_code == 200
    assert client.get("/program").get_json()["is_custom"] is False


def test_exercise_library_endpoint(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    d = client.get("/exercise_library").get_json()
    assert "Goblet Squat" in d["exercises"] and len(d["exercises"]) > 10
