"""Shared fixtures: a fake in-memory Mongo layer patched into every app module,
so the whole stack is testable without a database or API keys."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("GROQ_API_KEY", "dummy")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest


def _match(doc: dict, q: dict) -> bool:
    for k, cond in q.items():
        v = doc.get(k)
        if isinstance(cond, dict):
            if "$regex" in cond and not str(v or "").startswith(cond["$regex"].lstrip("^")):
                return False
            if "$gte" in cond and not (v is not None and v >= cond["$gte"]):
                return False
            if "$lte" in cond and not (v is not None and v <= cond["$lte"]):
                return False
        elif v != cond:
            return False
    return True


class FakeCursor:
    def __init__(self, data):
        self.data = data

    def __iter__(self):
        return iter(self.data)

    def sort(self, *args, **kwargs):
        key, direction = "_id", 1
        if args:
            if isinstance(args[0], str):
                key = args[0]
                direction = args[1] if len(args) > 1 else 1
            elif isinstance(args[0], list) and args[0]:
                key, direction = args[0][0]
        self.data = sorted(self.data, key=lambda d: str(d.get(key, "")),
                           reverse=(direction == -1))
        return self

    def limit(self, n):
        self.data = self.data[:n]
        return self


class FakeCol:
    def __init__(self):
        self.docs = {}      # fixed-_id documents (profile/log/mem/...)
        self.rows = []      # insert_one'd rows (expenses/meals/audit/...)
        self._seq = 0

    def find_one(self, q=None, sort=None):
        if q and "_id" in q and not isinstance(q["_id"], dict):
            d = self.docs.get(q["_id"])
            if d:
                return dict(d)
            return next((dict(r) for r in self.rows if r.get("_id") == q["_id"]), None)
        matches = [r for r in list(self.docs.values()) + self.rows if _match(r, q or {})]
        if not matches:
            return None
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda d: str(d.get(key, "")), reverse=(direction == -1))
        return dict(matches[0])

    def update_one(self, q, u, upsert=False):
        _id = q.get("_id")
        doc = self.docs.get(_id)
        if doc is None:                    # also update insert_one'd rows by _id
            doc = next((r for r in self.rows if r.get("_id") == _id), None)
        inserted = False
        if doc is None and upsert:
            doc = {"_id": _id}
            self.docs[_id] = doc
            inserted = True
        if doc is None:
            return
        for k, v in u.get("$set", {}).items():
            if k.startswith("sessions."):
                doc["sessions"][int(k.split(".", 1)[1])] = v
            else:
                doc[k] = v
        if inserted:
            for k, v in u.get("$setOnInsert", {}).items():
                doc[k] = v
        for k, v in u.get("$push", {}).items():
            doc.setdefault(k, []).append(v)
        for k, cond in u.get("$pull", {}).items():
            doc[k] = [x for x in doc.get(k, []) if not _match(x, cond)]

    def insert_one(self, d):
        d = dict(d)
        if "_id" not in d:
            self._seq += 1
            d["_id"] = f"fid{self._seq:06d}"
        self.rows.append(d)
        return SimpleNamespace(inserted_id=d["_id"])

    def find(self, q=None):
        data = [dict(x) for x in list(self.docs.values()) + self.rows
                if _match(x, q or {})]
        return FakeCursor(data)

    def delete_one(self, q):
        _id = q.get("_id")
        for i, r in enumerate(self.rows):
            if r.get("_id") == _id:
                self.rows.pop(i)
                return
        self.docs.pop(_id, None)

    def delete_many(self, q):
        before = len(self.rows) + len(self.docs)
        if not q:
            self.rows, self.docs = [], {}
        else:
            self.rows = [r for r in self.rows if not _match(r, q)]
            self.docs = {k: v for k, v in self.docs.items() if not _match(v, q)}
        return SimpleNamespace(deleted_count=before - len(self.rows) - len(self.docs))


class _Db(dict):
    """Collection dict that auto-creates a FakeCol on first access."""
    def __missing__(self, key):
        self[key] = FakeCol()
        return self[key]


@pytest.fixture
def db(monkeypatch):
    """Fake DB: returns the collection dict; patches _col in every app module."""
    cols = _Db()

    def fake_col(name):
        return cols[name]

    import agent_core
    monkeypatch.setattr(agent_core, "_col", fake_col)
    import bot  # noqa: F401 — pulls in the full module graph
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", "") or ""
        if f.startswith(str(ROOT)) and hasattr(mod, "_col"):
            monkeypatch.setattr(mod, "_col", fake_col, raising=False)
    # Never let a test hit a real LLM
    import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "", raising=False)
    return cols


@pytest.fixture
def client(db):
    import bot
    return bot.flask_app.test_client()


@pytest.fixture
def profile_doc():
    return {"_id": "user", "name": "K", "age": 28, "weight_kg": 97,
            "height_cm": 178, "goal": "lose fat", "level": "intermediate",
            "days_per_week": 6, "diet": "veg", "session_min": "45",
            "activity_level": "sedentary"}
