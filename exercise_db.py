"""
Exercise demos — start/end photos + step-by-step form instructions for any
lift in the program, from the public-domain free-exercise-db (Unlicense,
github.com/yuhonas/free-exercise-db, ~870 exercises). The two frames are
alternated client-side so they read as a simple animation.

The dataset is fetched once per process and cached in memory; names are
matched fuzzily (token overlap, dumbbell-biased since that's the equipment).
"""

import logging
import re
import threading

import requests

log = logging.getLogger(__name__)

DATA_URL = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json"
IMG_BASE = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/"

_data: list | None = None
_lock = threading.Lock()

_ALIASES = {
    "db": "dumbbell", "dbs": "dumbbell", "dumbbells": "dumbbell", "bb": "barbell",
    "ohp": "shoulder press", "rdl": "romanian deadlift", "bw": "bodyweight",
    "presses": "press", "raises": "raise", "curls": "curl", "rows": "row",
    "extensions": "extension", "flyes": "fly", "flies": "fly", "flye": "fly",
    "squats": "squat", "lunges": "lunge", "pushups": "pushup", "push": "push",
    "tricep": "triceps", "bicep": "biceps", "lateral": "lateral", "lat": "lateral",
}
_STOP = {"the", "a", "with", "on", "and", "of", "to", "-"}


def _tokens(name: str) -> set[str]:
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("push-up", "pushup").replace("push up", "pushup"))
    out = set()
    for w in s.split():
        w = _ALIASES.get(w, w)
        out.update(x for x in w.split() if x not in _STOP)
    return out


def _fix_text(t: str) -> str:
    """The dataset has some UTF-8 double-encoding (e.g. 'Â°'); undo it."""
    try:
        return t.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return t


def _load() -> list:
    global _data
    if _data is not None:
        return _data
    with _lock:
        if _data is None:
            r = requests.get(DATA_URL, timeout=20)
            r.raise_for_status()
            _data = r.json()
            log.info(f"Loaded {len(_data)} exercises from free-exercise-db")
    return _data


def _score(query: set[str], ex: dict) -> float:
    cand = _tokens(ex.get("name", ""))
    if not cand or not query:
        return 0.0
    inter = len(query & cand)
    if not inter:
        return 0.0
    score = inter / len(query | cand)
    if ex.get("equipment") == "dumbbell":
        score += 0.08                        # home setup is dumbbells + bench
    elif ex.get("equipment") in ("body only", None):
        score += 0.03 if "dumbbell" not in query else -0.05
    elif "dumbbell" in query:
        score -= 0.1
    return score


def _shape(ex: dict) -> dict:
    return {
        "name": ex.get("name"),
        "images": [IMG_BASE + p for p in ex.get("images", [])[:2]],
        "instructions": [_fix_text(s) for s in ex.get("instructions", [])],
        "primary": ex.get("primaryMuscles", []),
        "secondary": ex.get("secondaryMuscles", []),
        "equipment": ex.get("equipment"),
        "level": ex.get("level"),
    }


def find_demo(name: str, data: list | None = None) -> dict:
    """Best demo match for `name` plus a few alternatives to pick from."""
    try:
        rows = data if data is not None else _load()
    except Exception as e:
        log.warning(f"exercise db unavailable: {e}")
        return {"error": "Exercise library is unreachable right now — try again shortly."}
    q = _tokens(name)
    ranked = sorted(((s, ex) for ex in rows if (s := _score(q, ex)) > 0),
                    key=lambda t: -t[0])
    if not ranked or ranked[0][0] < 0.2:
        return {"error": f"No demo found for “{name}”."}
    best = _shape(ranked[0][1])
    best["others"] = [ex.get("name") for _, ex in ranked[1:5]]
    return best


def demo_by_exact(name: str, data: list | None = None) -> dict:
    rows = data if data is not None else _load()
    ex = next((e for e in rows if e.get("name", "").lower() == name.lower()), None)
    return _shape(ex) if ex else find_demo(name, rows)
