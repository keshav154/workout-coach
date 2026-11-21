"""
Web Push for the rest timer — the end-of-rest alert reaches the phone even
when the screen is locked or the app is backgrounded (in-page JS timers get
frozen there, so the old beep never fired).

Flow: the page subscribes once (pushManager.subscribe with our VAPID public
key) and POSTs the subscription here. Starting a rest POSTs the duration; a
server-side timer fires a push when it ends. Stopping/restarting cancels it.

VAPID keys are generated on first use and persisted in Mongo, so there's no
env setup and subscriptions survive restarts. VAPID_PRIVATE_PEM /
VAPID_PUBLIC_KEY env vars override if you'd rather manage them yourself.
"""

import base64
import json
import logging
import os
import threading
import time

from agent_core import _col

log = logging.getLogger(__name__)

_lock = threading.Lock()
_timers: dict[str, threading.Timer] = {}     # one pending rest alert per device
_keys_cache: dict | None = None
MAX_REST_S = 15 * 60


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _keys() -> dict:
    """{'private_pem', 'public'} — env, else Mongo, else freshly generated."""
    global _keys_cache
    if _keys_cache:
        return _keys_cache
    env_priv = os.environ.get("VAPID_PRIVATE_PEM", "").strip()
    env_pub = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    if env_priv and env_pub:
        _keys_cache = {"private_pem": env_priv.replace("\\n", "\n"), "public": env_pub}
        return _keys_cache
    doc = _col("settings").find_one({"_id": "vapid"})
    if doc and doc.get("private_pem") and doc.get("public"):
        _keys_cache = {"private_pem": doc["private_pem"], "public": doc["public"]}
        return _keys_cache
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pk = ec.generate_private_key(ec.SECP256R1())
    pem = pk.private_bytes(serialization.Encoding.PEM,
                           serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    pub = _b64url(pk.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint))
    _col("settings").update_one({"_id": "vapid"},
                                {"$set": {"private_pem": pem, "public": pub}}, upsert=True)
    _keys_cache = {"private_pem": pem, "public": pub}
    log.info("Generated and stored new VAPID keys for web push")
    return _keys_cache


def public_key() -> str:
    return _keys()["public"]


def _device_id(sub: dict) -> str:
    return (sub.get("endpoint") or "")[-64:]


def save_subscription(sub: dict, origin: str) -> bool:
    if not isinstance(sub, dict) or not str(sub.get("endpoint", "")).startswith("https://") \
            or not isinstance(sub.get("keys"), dict):
        return False
    _col("push_subs").update_one(
        {"_id": _device_id(sub)},
        {"$set": {"sub": {"endpoint": sub["endpoint"], "keys": sub["keys"]},
                  "origin": origin, "ts": int(time.time())}},
        upsert=True)
    return True


def send(sub: dict, origin: str, payload: dict) -> bool:
    """Deliver one push. Drops the subscription if the push service says it's gone."""
    try:
        from py_vapid import Vapid01
        from pywebpush import WebPushException, webpush
    except ImportError:
        log.warning("pywebpush not installed — rest-timer push disabled")
        return False
    try:
        vapid = Vapid01.from_pem(_keys()["private_pem"].encode())
        # Apple requires a valid https: or mailto: subject; use the app's own URL
        # (no personal email leaves the server). Fresh dict: webpush mutates it.
        webpush(subscription_info=sub, data=json.dumps(payload),
                vapid_private_key=vapid,
                vapid_claims={"sub": origin.rstrip("/") or "https://localhost"},
                ttl=120, timeout=10)
        return True
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            _col("push_subs").delete_one({"_id": _device_id(sub)})
        log.warning(f"Web push failed ({status}): {e}")
    except Exception as e:
        log.warning(f"Web push error: {e}")
    return False


def _fire(device: str) -> None:
    with _lock:
        _timers.pop(device, None)
    doc = _col("push_subs").find_one({"_id": device})
    if doc:
        send(doc["sub"], doc.get("origin", ""), {
            "title": "Rest over — GO! 💪",
            "body": "Time for your next set.",
            "tag": "rest-timer",
        })


def schedule_rest(sub: dict, origin: str, seconds: float) -> bool:
    """(Re)start the rest alert for this device. Returns False if invalid."""
    if not save_subscription(sub, origin):
        return False
    seconds = max(5.0, min(float(seconds), MAX_REST_S))
    device = _device_id(sub)
    cancel_rest(device)
    t = threading.Timer(seconds, _fire, args=(device,))
    t.daemon = True
    with _lock:
        _timers[device] = t
    t.start()
    return True


def cancel_rest(device_or_sub) -> None:
    device = _device_id(device_or_sub) if isinstance(device_or_sub, dict) else device_or_sub
    with _lock:
        t = _timers.pop(device, None)
    if t:
        t.cancel()


def pending_count() -> int:
    with _lock:
        return len(_timers)
