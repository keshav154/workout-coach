"""Water tracking: module logic, endpoints, and the log_water tool."""

import water
import write_tools


def test_water_goal_from_bodyweight(db, profile_doc):
    p = dict(profile_doc)          # 97 kg -> ~35ml/kg = 3395 -> rounded to 3500
    assert water.water_goal_ml(p) == 3500
    p["water_goal_ml"] = 4000      # custom overrides
    assert water.water_goal_ml(p) == 4000
    assert water.water_goal_ml({"weight_kg": 40}) == 2000   # clamped up
    assert water.water_goal_ml({"weight_kg": 200}) == 4500  # clamped down


def test_add_undo_and_total(db):
    water.add_water(250)
    water.add_water(500)
    assert water.water_today() == {"ml": 750, "count": 2}
    assert water.undo_water() is True
    assert water.water_today() == {"ml": 250, "count": 1}
    water.undo_water()
    assert water.undo_water() is False          # nothing left
    assert water.water_today()["ml"] == 0


def test_set_total(db):
    water.add_water(250)
    water.set_water_total(2000)                 # "I've had 2 litres today"
    assert water.water_today() == {"ml": 2000, "count": 1}


def test_water_week_length(db):
    water.add_water(1000)
    wk = water.water_week()
    assert len(wk) == 7 and wk[-1]["ml"] == 1000 and wk[0]["ml"] == 0


def test_water_endpoints(client, db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    d = client.get("/water").get_json()
    assert d["ml"] == 0 and d["goal"] == 3500 and d["glass_ml"] == 250

    r = client.post("/water_add", json={"ml": 250}).get_json()
    assert r["ml"] == 250 and r["count"] == 1
    client.post("/water_add", json={"ml": 500})
    assert client.get("/water").get_json()["ml"] == 750

    r = client.post("/water_undo").get_json()
    assert r["ml"] == 250

    assert client.post("/water_add", json={"ml": 99999}).status_code == 400
    assert client.post("/water_add", json={"ml": 0}).status_code == 400


def test_log_water_tool(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    tools = write_tools.make_write_tools({})
    assert tools["log_water"](glasses=2).startswith("SAVED")
    assert water.water_today()["ml"] == 500
    tools["log_water"](ml=300)
    assert water.water_today()["ml"] == 800
    tools["log_water"](set_total_litres=2)      # overwrite
    assert water.water_today()["ml"] == 2000
    assert "REJECTED" in tools["log_water"](ml=99999)


def test_log_water_default_one_glass(db, profile_doc):
    db["profile"].docs["user"] = dict(profile_doc)
    tools = write_tools.make_write_tools({})
    tools["log_water"]()                        # no args -> one glass
    assert water.water_today()["ml"] == 250
