"""Identity & auth helpers — isolated from graph imports so the security
surface can be tested without GCP dependencies.

Three caller kinds (AuthContext.kind):
- "internal": presents the static API_AUTH_TOKEN (constant-time compared).
  May assert a user_id; the only kind allowed near debug routes.
- "firebase": presents a Firebase ID token (Google Sign-In), verified via
  the Admin SDK. Authenticated but NOT trusted — resolves to a stable
  internal user_id through auth_identities; can never assert identity or
  reach debug routes.
- "anon": no Authorization header. Identity derives ONLY from a
  server-issued session (HttpOnly cookie or X-Vital-Session header).

Rules that must never soften:
- A present-but-invalid bearer is a hard 401 — no anonymous fallback.
- Verification failures return one generic message (no expired/project/
  signature detail for attackers to fingerprint).
- Once an anonymous identity is linked to an account, the bare session
  stops resolving to it: signing out really signs you out server-side.
"""
import re
import secrets
import uuid
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Header, HTTPException

from vital.config import settings

SESSION_COOKIE = "vital_session"
_SESSION_RE = re.compile(r"^[0-9a-f]{32}$")

def _invalid_credentials() -> HTTPException:
    """Fresh exception per failure — re-raising one shared instance would
    keep extending its traceback, leaking memory under bad-token spam.
    One generic message for every failure mode (no fingerprinting)."""
    return HTTPException(status_code=401, detail="invalid credentials")


@dataclass(frozen=True)
class AuthContext:
    kind: str                 # "anon" | "internal" | "firebase"
    subject: str | None = None  # Firebase UID for kind="firebase"

    @property
    def trusted(self) -> bool:
        return self.kind == "internal"


def configured_token() -> str | None:
    """The auth token, treating blank/whitespace-only as NOT configured —
    an empty secret must never silently disable auth checks."""
    token = settings().api_auth_token
    if token and token.strip():
        return token.strip()
    return None


@lru_cache
def _firebase_app():
    """Admin SDK app, initialized once with ADC (Cloud Run service identity
    locally: `gcloud auth application-default login`). No JSON key, ever."""
    import firebase_admin
    from firebase_admin import credentials

    return firebase_admin.initialize_app(
        credentials.ApplicationDefault(),
        {"projectId": settings().firebase_project_id})


def _firebase_verify(token: str) -> dict:
    """Isolated so tests monkeypatch THIS, never the network. Raises on any
    invalid token (expired, malformed, wrong project, bad signature)."""
    from firebase_admin import auth as fb_auth

    return fb_auth.verify_id_token(token, app=_firebase_app())


def authenticate(authorization: str | None = Header(default=None)) -> AuthContext:
    """Single entry point for the Authorization header.

    Missing header → anonymous. A present bearer must be EITHER the internal
    token or (when enabled) a valid Firebase ID token — anything else is a
    hard 401 with a generic message. Never a silent anonymous downgrade,
    which would hand a broken/expired client someone else's fresh identity.
    """
    if authorization is None:
        return AuthContext("anon")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401,
                            detail="expected 'Authorization: Bearer <token>'")
    value = authorization[len("Bearer "):].strip()
    internal = configured_token()
    if internal is not None and secrets.compare_digest(value, internal):
        return AuthContext("internal")
    if settings().firebase_auth_enabled:
        try:
            decoded = _firebase_verify(value)
            subject = decoded.get("uid") or decoded.get("sub")
        except Exception:
            raise _invalid_credentials()
        if not subject:
            raise _invalid_credentials()
        return AuthContext("firebase", subject=str(subject))
    raise _invalid_credentials()


def caller_is_trusted(authorization: str | None = Header(default=None)) -> bool:
    """True ONLY for the static internal token — Firebase users are
    authenticated, not trusted. Kept as the debug-route gate."""
    return authenticate(authorization).trusted


def resolve_identity(req_user_id: str, auth: AuthContext,
                     session_cookie: str | None) -> tuple[str, str | None]:
    """Returns (internal_user_id, new_session_id_to_set_or_None).

    internal → the asserted user_id (existing behavior, unchanged).
    firebase → stable internal id via auth_identities; a valid anonymous
               session may be claimed on FIRST sign-in so pre-sign-in data
               follows the account. The candidate is server-derived.
    anon     → session-derived id, UNLESS that id has been linked to an
               account — then the bare session is rejected and rotated, so
               signed-out browsers can't keep reading account data.
    """
    from vital import storage

    if auth.kind == "internal":
        return req_user_id, None

    valid_session = bool(session_cookie and _SESSION_RE.match(session_cookie))

    if auth.kind == "firebase":
        candidate = f"anon-{session_cookie}" if valid_session else None
        user_id = storage.resolve_external_identity("firebase", auth.subject,
                                                    candidate)
        return user_id, None

    if valid_session:
        user_id = f"anon-{session_cookie}"
        if not storage.user_id_is_linked(user_id):
            return user_id, None
        # linked to an account → this session alone no longer grants access
    new_session = uuid.uuid4().hex
    return f"anon-{new_session}", new_session


def validate_startup() -> None:
    """Fail-closed boot checks. Called from the app lifespan."""
    cfg = settings()
    if cfg.debug_endpoints and configured_token() is None:
        raise RuntimeError(
            "Refusing to start: DEBUG_ENDPOINTS=true requires a non-empty "
            "API_AUTH_TOKEN (debug routes must never be unauthenticated)."
        )
    if cfg.session_cookie_samesite not in ("lax", "none", "strict"):
        raise RuntimeError("SESSION_COOKIE_SAMESITE must be lax, none, or strict")
    if cfg.session_cookie_samesite == "none" and not cfg.session_cookie_secure:
        raise RuntimeError(
            "Refusing to start: SameSite=None cookies MUST be Secure — "
            "browsers reject them otherwise, and plaintext cross-site "
            "cookies would be wrong anyway.")
    if cfg.firebase_auth_enabled:
        if not (cfg.firebase_project_id or "").strip():
            raise RuntimeError(
                "Refusing to start: FIREBASE_AUTH_ENABLED=true requires "
                "FIREBASE_PROJECT_ID.")
        try:
            _firebase_app()
        except Exception as exc:
            raise RuntimeError(
                f"Refusing to start: Firebase Admin init failed "
                f"({type(exc).__name__}). Check ADC and FIREBASE_PROJECT_ID."
            ) from exc
