"""
Passkey (WebAuthn) unlock — Face ID / fingerprint instead of typing
WEB_PASSWORD. A passkey can only be registered by an already-unlocked client;
a successful passkey login returns a signed, time-limited token the page then
sends as X-Auth-Token (accepted by require_auth alongside X-Password).

Credentials live in the `passkeys` collection; the one-time challenge rides
in Flask's signed session cookie.
"""

import base64
import logging
import os
import time
from urllib.parse import urlparse

from agent_core import _col

log = logging.getLogger(__name__)

TOKEN_MAX_AGE = int(os.environ.get("PASSKEY_TOKEN_DAYS", "7")) * 86400
RP_NAME = "CoachxKeshav"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def rp_and_origin(request) -> tuple[str, str]:
    """RP ID = this host; expected origin = the browser's Origin if (and only
    if) it's for this same host. Raises ValueError otherwise."""
    host = (request.host or "").split(":")[0]
    origin = request.headers.get("Origin", "")
    if not host or urlparse(origin).hostname != host:
        raise ValueError("origin mismatch")
    return host, origin


def _serializer(secret: str):
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(secret, salt="passkey-auth")


def issue_token(secret: str, cred_id: str) -> str:
    return _serializer(secret).dumps({"cred": cred_id})


def token_valid(secret: str, token: str) -> bool:
    if not token:
        return False
    try:
        data = _serializer(secret).loads(token, max_age=TOKEN_MAX_AGE)
    except Exception:
        return False
    # Revoked passkeys invalidate their tokens.
    return bool(_col("passkeys").find_one({"_id": data.get("cred", "")}))


def count() -> int:
    return len(list(_col("passkeys").find()))


def registration_options(request, session) -> str:
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                          PublicKeyCredentialDescriptor,
                                          ResidentKeyRequirement,
                                          UserVerificationRequirement)
    rp_id, _ = rp_and_origin(request)
    existing = [PublicKeyCredentialDescriptor(id=_unb64(d["_id"]))
                for d in _col("passkeys").find()]
    opts = generate_registration_options(
        rp_id=rp_id, rp_name=RP_NAME, user_name="me", user_id=b"coach-user",
        user_display_name=RP_NAME, exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED),
    )
    session["pk_reg_challenge"] = _b64(opts.challenge)
    return options_to_json(opts)


def verify_registration(request, session, credential: dict, label: str = "") -> str:
    from webauthn import verify_registration_response
    rp_id, origin = rp_and_origin(request)
    challenge = session.pop("pk_reg_challenge", None)
    if not challenge:
        raise ValueError("no registration in progress")
    v = verify_registration_response(
        credential=credential, expected_challenge=_unb64(challenge),
        expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True)
    cred_id = _b64(v.credential_id)
    _col("passkeys").update_one({"_id": cred_id}, {"$set": {
        "public_key": _b64(v.credential_public_key),
        "sign_count": v.sign_count,
        "label": (label or "Passkey")[:40],
        "created": int(time.time()),
    }}, upsert=True)
    return cred_id


def login_options(request, session) -> str:
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import (PublicKeyCredentialDescriptor,
                                          UserVerificationRequirement)
    rp_id, _ = rp_and_origin(request)
    allow = [PublicKeyCredentialDescriptor(id=_unb64(d["_id"])) for d in _col("passkeys").find()]
    if not allow:
        raise LookupError("no passkeys registered")
    opts = generate_authentication_options(
        rp_id=rp_id, allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED)
    session["pk_auth_challenge"] = _b64(opts.challenge)
    return options_to_json(opts)


def verify_login(request, session, credential: dict) -> str:
    """Returns the credential id on success; raises on failure."""
    from webauthn import verify_authentication_response
    rp_id, origin = rp_and_origin(request)
    challenge = session.pop("pk_auth_challenge", None)
    if not challenge:
        raise ValueError("no login in progress")
    cred_id = str(credential.get("id") or credential.get("rawId") or "")
    doc = _col("passkeys").find_one({"_id": cred_id})
    if not doc:
        raise LookupError("unknown passkey")
    v = verify_authentication_response(
        credential=credential, expected_challenge=_unb64(challenge),
        expected_rp_id=rp_id, expected_origin=origin,
        credential_public_key=_unb64(doc["public_key"]),
        credential_current_sign_count=int(doc.get("sign_count", 0)),
        require_user_verification=True)
    _col("passkeys").update_one({"_id": cred_id}, {"$set": {"sign_count": v.new_sign_count,
                                                           "last_used": int(time.time())}})
    return cred_id


def list_passkeys() -> list[dict]:
    return [{"id": d["_id"], "label": d.get("label", "Passkey"),
             "created": d.get("created"), "last_used": d.get("last_used")}
            for d in _col("passkeys").find()]


def delete_passkey(cred_id: str) -> bool:
    if not _col("passkeys").find_one({"_id": cred_id}):
        return False
    _col("passkeys").delete_one({"_id": cred_id})
    return True
