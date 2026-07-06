"""Identity & auth helpers — isolated from graph imports so the security
surface can be tested without GCP dependencies.

Interim model until real per-user auth (Phase 5):
- Trusted callers (valid bearer token) may assert a user_id.
- Anonymous callers get a SERVER-generated session identity via cookie:
  they can continue their own conversation but can never name their own
  identity or thread namespace — so they can't collide with or guess
  their way into anyone else's state.
- DEBUG_ENDPOINTS=true refuses to boot without a token (fail closed).
"""
import re
import secrets
import uuid

from fastapi import Header, HTTPException

from vital.config import settings

SESSION_COOKIE = "vital_session"
_SESSION_RE = re.compile(r"^[0-9a-f]{32}$")


def configured_token() -> str | None:
    """The auth token, treating blank/whitespace-only as NOT configured —
    an empty secret must never silently disable auth checks."""
    token = settings().api_auth_token
    if token and token.strip():
        return token.strip()
    return None


def caller_is_trusted(authorization: str | None = Header(default=None)) -> bool:
    """True only for callers presenting 'Authorization: Bearer <token>'.

    Wrong token or malformed scheme → hard 401 (not a silent downgrade,
    which would mask misconfigured clients). Missing header → anonymous.
    """
    token = configured_token()
    if token is None or authorization is None:
        return False
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401,
                            detail="expected 'Authorization: Bearer <token>'")
    if not secrets.compare_digest(authorization[len("Bearer "):].strip(), token):
        raise HTTPException(status_code=401, detail="invalid token")
    return True


def resolve_identity(req_user_id: str, trusted: bool,
                     session_cookie: str | None) -> tuple[str, str | None]:
    """Returns (user_id, new_session_id_to_set_or_None).

    Trusted callers: their asserted user_id, no cookie handling.
    Anonymous callers: identity derives ONLY from a server-issued session
    cookie (validated format). Absent/invalid cookie → fresh session, so
    request-body values can never select an existing namespace.
    """
    if trusted:
        return req_user_id, None
    if session_cookie and _SESSION_RE.match(session_cookie):
        return f"anon-{session_cookie}", None
    new_session = uuid.uuid4().hex
    return f"anon-{new_session}", new_session


def validate_startup() -> None:
    """Fail-closed boot checks. Called from the app lifespan."""
    if settings().debug_endpoints and configured_token() is None:
        raise RuntimeError(
            "Refusing to start: DEBUG_ENDPOINTS=true requires a non-empty "
            "API_AUTH_TOKEN (debug routes must never be unauthenticated)."
        )
