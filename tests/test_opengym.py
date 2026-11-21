"""OpenGym-inspired features: do-a-different-day without shifting the plan,
remembered supersets, full-history calendar, detrained muscles, exercise
demos, rest-timer push plumbing and passkey auth."""

from agent_core import get_next_day, get_rotation, load_log, today_iso


def _seed(db, profile_doc, sessions):
    db["profile"].docs["user"] = dict(profile_doc)
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": sessions}


def _sess(day, date, name="Dumbbell Flat Bench Press", **kw):
    return {"day": day, "date": date, "exercises": [{"name": name, "weight": 18, "reps_done": 10}], **kw}


# ── Do a different day today (plan unchanged) ────────────────────────────────
def test_out_of_order_session_does_not_move_rotation(db):
    rot = get_rotation()
    db["workout_log"].docs["log"] = {"_id": "log", "sessions": [
        _sess(rot[0], "2026-08-01"),
        _sess(rot[3], "2026-08-02", out_of_order=True),
    ]}
    assert get_next_day(load_log()) == rot[1]


def test_today_program_day_override(client, db, profile_doc):
    rot = get_rotation()
    _seed(db, profile_doc, [_sess(rot[0], "2026-08-01")])
    d = client.get(f"/today_program?day={rot[3]}").get_json()
    assert d["day"] == rot[3] and d["scheduled_day"] == rot[1] and d["override"] is True
    d = client.get("/today_program?day=ZZ").get_json()           # unknown -> scheduled
    assert d["day"] == rot[1] and d["override"] is False


def test_logging_override_keeps_plan(client, db, profile_doc):
    rot = get_rotation()
    _seed(db, profile_doc, [_sess(rot[0], "2026-08-01")])
    r = client.post("/log_workout", json={"day": rot[3], "out_of_order": True, "exercises": [
        {"name": "Dumbbell Row", "sets": [{"weight": 16, "reps": 10}]}]})
    assert r.status_code == 200
    sess = load_log()["sessions"]
    assert sess[-1]["out_of_order"] is True
    assert get_next_day(load_log()) == rot[1]


def test_scheduled_day_is_never_flagged_out_of_order(client, db, profile_doc):
    rot = get_rotation()
    _seed(db, profile_doc, [_sess(rot[0], "2026-08-01")])
    client.post("/log_workout", json={"day": rot[1], "out_of_order": True, "exercises": [
        {"name": "Dumbbell Row", "sets": [{"weight": 16, "reps": 10}]}]})
    assert "out_of_order" not in load_log()["sessions"][-1]
    assert get_next_day(load_log()) == rot[2]


# ── Supersets remembered per day ─────────────────────────────────────────────
def test_superset_pairings_are_remembered(client, db, profile_doc):
    rot = get_rotation()
    _seed(db, profile_doc, [])
    day = rot[0]
    from agent_core import get_program
    names = [e["name"] for e in get_program()[day]["exercises"]]
    client.post("/log_workout", json={"day": day, "exercises": [
        {"name": names[0], "sets": [{"weight": 16, "reps": 10}], "superset": "A"},
        {"name": names[1], "sets": [{"weight": 10, "reps": 12}], "superset": "A"},
        {"name": names[2], "sets": [{"weight": 10, "reps": 12}]},
    ]})
    assert db["profile"].docs["user"]["supersets"][day] == [names[1]]
    # Next time this day comes up, the pairing is pre-linked.
    db["workout_log"].docs["log"]["sessions"][-1]["out_of_order"] = True
    d = client.get(f"/today_program?day={day}").get_json()
    linked = {e["name"]: e["superset_linked"] for e in d["exercises"]}
    assert linked[names[1]] is True and linked[names[0]] is False and linked[names[2]] is False


# ── Full-history training calendar ───────────────────────────────────────────
def test_stats_heatmap_covers_full_history(client, db, profile_doc):
    from datetime import date, timedelta
    t = date.fromisoformat(today_iso())
    days = [(t - timedelta(days=n)).isoformat() for n in (78, 77, 76, 40, 1)]
    _seed(db, profile_doc, [_sess("A", d) for d in days])
    d = client.get("/stats").get_json()
    hm, meta = d["heatmap"], d["heatmap_meta"]
    assert hm[0]["dow"] == 0                                 # Monday-aligned
    assert hm[0]["date"] <= days[0] and hm[-1]["date"] == today_iso()
    assert meta["training_days"] == 5 and meta["best_streak"] == 3
    assert meta["first_date"] == days[0] and meta["span_days"] == 79


# ── Detrained muscles ────────────────────────────────────────────────────────
def test_freshness_flags_detrained(db):
    from datetime import date, timedelta
    from freshness import muscle_freshness
    t = date.fromisoformat(today_iso())
    log = {"sessions": [
        _sess("A", (t - timedelta(days=1)).isoformat(), name="Dumbbell Flat Bench Press"),
        _sess("B", (t - timedelta(days=12)).isoformat(), name="Dumbbell Bicep Curl"),
    ]}
    f = muscle_freshness(log)
    assert f["Chest"]["detrained"] is False and f["Chest"]["days_since"] == 1
    assert f["Biceps"]["detrained"] is True and f["Biceps"]["days_since"] == 12
    assert f["Core"]["detrained"] is True and f["Core"]["days_since"] is None


# ── Exercise demos ───────────────────────────────────────────────────────────
_DB = [
    {"name": "Dumbbell Bench Press", "equipment": "dumbbell", "images": ["Dumbbell_Bench_Press/0.jpg", "Dumbbell_Bench_Press/1.jpg"],
     "instructions": ["Lie back.", "Press up."], "primaryMuscles": ["chest"], "secondaryMuscles": ["triceps"]},
    {"name": "Barbell Bench Press - Medium Grip", "equipment": "barbell", "images": ["b/0.jpg"],
     "instructions": ["x"], "primaryMuscles": ["chest"], "secondaryMuscles": []},
    {"name": "Hammer Curls", "equipment": "dumbbell", "images": ["h/0.jpg", "h/1.jpg"],
     "instructions": ["Curl at 90Â° elbows."], "primaryMuscles": ["biceps"], "secondaryMuscles": []},
]


def test_demo_match_prefers_dumbbell_variant():
    from exercise_db import find_demo
    r = find_demo("Dumbbell Flat Bench Press", _DB)
    assert r["name"] == "Dumbbell Bench Press"
    assert r["images"][0].endswith("Dumbbell_Bench_Press/0.jpg") and len(r["images"]) == 2
    assert "Barbell Bench Press - Medium Grip" in r["others"]


def test_demo_aliases_and_text_fix():
    from exercise_db import find_demo
    r = find_demo("DB hammer curl", _DB)
    assert r["name"] == "Hammer Curls"
    assert r["instructions"] == ["Curl at 90° elbows."]
    assert "error" in find_demo("Zercher yoke carry", _DB)


def test_demo_route(client, db, monkeypatch):
    import exercise_db
    monkeypatch.setattr(exercise_db, "_data", _DB)
    r = client.get("/exercise_demo?name=Hammer%20Curl").get_json()
    assert r["name"] == "Hammer Curls"
    r = client.get("/exercise_demo?name=x&exact=Dumbbell%20Bench%20Press").get_json()
    assert r["name"] == "Dumbbell Bench Press"


# ── Rest-timer push ──────────────────────────────────────────────────────────
_SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123", "keys": {"p256dh": "k", "auth": "a"}}


def test_vapid_keys_generated_once_and_persisted(db, monkeypatch):
    import push
    monkeypatch.setattr(push, "_keys_cache", None)
    k1 = push.public_key()
    monkeypatch.setattr(push, "_keys_cache", None)
    assert push.public_key() == k1                        # reloaded from DB, not regenerated
    assert len(k1) == 87                                   # 65-byte uncompressed P-256 point, b64url


def test_rest_push_schedules_and_cancels(client, db, monkeypatch):
    import push
    fired = []
    monkeypatch.setattr(push, "send", lambda sub, origin, payload: fired.append(payload) or True)
    r = client.post("/push/rest", json={"seconds": 60, "subscription": _SUB})
    assert r.status_code == 200 and push.pending_count() == 1
    client.post("/push/cancel", json={"subscription": _SUB})
    assert push.pending_count() == 0
    push._fire(push._device_id(_SUB))                      # what the timer does at 0
    assert fired and fired[0]["tag"] == "rest-timer"


def test_rest_push_rejects_bad_subscription(client, db):
    r = client.post("/push/rest", json={"seconds": 60, "subscription": {"endpoint": "http://evil"}})
    assert r.status_code == 400


# ── Passkeys ─────────────────────────────────────────────────────────────────
def test_passkey_token_auth(client, db, monkeypatch):
    import bot
    import passkeys
    monkeypatch.setattr(bot, "WEB_PASSWORD", "secret")
    assert client.get("/day_info").status_code == 401
    db["passkeys"].docs["cred1"] = {"_id": "cred1", "public_key": "x", "sign_count": 0}
    tok = passkeys.issue_token(bot.FLASK_SECRET, "cred1")
    assert client.get("/day_info", headers={"X-Auth-Token": tok}).status_code == 200
    assert client.get("/day_info", headers={"X-Auth-Token": tok + "x"}).status_code == 401
    # Deleting the passkey revokes its tokens.
    assert passkeys.delete_passkey("cred1")
    assert client.get("/day_info", headers={"X-Auth-Token": tok}).status_code == 401


def test_passkey_status_and_registration_guard(client, db, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "WEB_PASSWORD", "secret")
    assert client.get("/passkey/status").get_json() == {"enabled": True, "registered": 0}
    # Registration needs an unlocked client...
    assert client.post("/passkey/register/options").status_code == 401
    # ...and an Origin matching this host.
    h = {"X-Password": "secret", "Origin": "https://evil.example"}
    assert client.post("/passkey/register/options", headers=h).status_code == 400
    h["Origin"] = "http://localhost"
    r = client.post("/passkey/register/options", headers=h)
    assert r.status_code == 200 and r.get_json()["rp"]["id"] == "localhost"
    # Login with none registered -> 404
    assert client.post("/passkey/login/options", headers={"Origin": "http://localhost"}).status_code == 404
