"""Identity & auth helpers — isolated from graph imports so the security
surface can be tested without GCP dependencies.

Interim model until real per-user auth (Phase 5):
- No API_AUTH_TOKEN configured → every caller is anonymous.
- Token configured: missing header → anonymous; wrong token → hard 401
  (not a silent downgrade, which would mask misconfigured clients).
- Only trusted callers may assert a user_id; everyone else is pinned
  to 'local-user' and can never touch another user's threads.
"""
import secrets

from fastapi import Header, HTTPException

from vital.config import settings


def caller_is_trusted(authorization: str | None = Header(default=None)) -> bool:
    token = settings().api_auth_token
    if token is None or authorization is None:
        return False
    provided = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="invalid token")
    return True


def resolve_user_id(req_user_id: str, trusted: bool) -> str:
    return req_user_id if trusted else "local-user"
