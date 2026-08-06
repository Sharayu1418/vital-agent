"""The `state` parameter for provider OAuth flows.

WHAT GOES WRONG WITHOUT IT
--------------------------
The callback is a GET the browser is redirected to, carrying an
authorization code. If we accept any code that arrives, an attacker can
complete their own consent at Google, capture the resulting code, and feed
it to a victim's browser — linking the ATTACKER's Fitbit account to the
VICTIM's VITAL account. The victim then sees a stranger's sleep data
driving their forecast, and every plan built on it. That is login CSRF,
and `state` is the standard defence.

The design mirrors the rule that already protects commit_plan: identity is
never taken from the request. Here the state binds the flow to the session
that STARTED it, and the callback derives the user from that session
rather than from anything in the URL.

Stateless on purpose: signed with the app's own secret and carrying its own
expiry, so no table, no cleanup job, and nothing to leak. Single-use is
approximated by the short TTL — a replay window of ten minutes against an
attacker who must also control the victim's browser.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from vital.config import settings

TTL_SECONDS = 600          # long enough to read a consent screen, no longer


class StateError(ValueError):
    """State missing, malformed, expired, or not ours."""


def _key() -> bytes:
    """Signing key.

    Reuses TOKEN_ENCRYPTION_KEY rather than adding another secret: both
    protect the same flow, and an extra key is one more thing to forget to
    set. Hashed first so the signing key is never the encryption key
    itself.
    """
    cfg = settings()
    material = (cfg.token_encryption_key or cfg.api_auth_token or "")
    if not material:
        raise StateError(
            "no secret available to sign the OAuth state — set "
            "TOKEN_ENCRYPTION_KEY before enabling provider connections")
    return hashlib.sha256(f"oauth-state:{material}".encode()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue(session_id: str, provider: str) -> str:
    """Mint a state bound to this session and provider."""
    payload = {
        "s": hashlib.sha256((session_id or "").encode()).hexdigest()[:32],
        "p": provider,
        "e": int(time.time()) + TTL_SECONDS,
        "n": secrets.token_urlsafe(8),
    }
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(_key(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def verify(state: str, session_id: str, provider: str) -> None:
    """Raise StateError unless this state was issued to this session.

    Order matters: the signature is checked before anything inside is
    read, so a forged payload never reaches the expiry or session logic.
    """
    if not state or "." not in state:
        raise StateError("missing or malformed state")
    body, _, signature = state.partition(".")

    expected = hmac.new(_key(), body.encode(), hashlib.sha256).digest()
    try:
        provided = _unb64(signature)
    except Exception as exc:
        raise StateError("state signature is not decodable") from exc
    # compare_digest, not ==: a short-circuiting comparison leaks the
    # signature one byte at a time to anyone who can time the callback.
    if not hmac.compare_digest(expected, provided):
        raise StateError("state signature does not verify")

    try:
        payload = json.loads(_unb64(body))
    except Exception as exc:
        raise StateError("state payload is not readable") from exc

    if payload.get("p") != provider:
        raise StateError("state was issued for a different provider")
    if int(payload.get("e", 0)) < time.time():
        raise StateError("state has expired — start the connection again")

    want = hashlib.sha256((session_id or "").encode()).hexdigest()[:32]
    if not hmac.compare_digest(str(payload.get("s", "")), want):
        raise StateError("state belongs to a different session")
