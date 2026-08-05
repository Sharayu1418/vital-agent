"""Communities tool — Reddit subreddit search adapter (D6).

Two transports behind one adapter, chosen by whether credentials exist:

- **App-only OAuth** (preferred). Reddit blocks unauthenticated requests from
  datacenter IPs, so the keyless endpoint failed on EVERY Cloud Run call and
  this feature was silently dead in production — the agent dutifully reported
  "community search is temporarily down" forever. Client-credentials auth
  needs no user account; we only read public subreddit listings.
- **Keyless JSON** (fallback). Still works from a laptop, so local dev and
  tests need no secrets. Kept deliberately: making credentials mandatory
  would break the zero-config local story for a non-core feature.

The agent sees neither. It gets our schema, or an `error` key, exactly as
before (D6). Meetup has no open API; Discord directories need scraping —
both can become additional providers here without touching the agent.
"""
import threading
import time

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel

from vital.config import settings

_KEYLESS_URL = "https://www.reddit.com/subreddits/search.json"
_OAUTH_URL = "https://oauth.reddit.com/subreddits/search"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

# Tokens last ~24h. Cache with a safety margin so one can never expire
# mid-flight, and guard with a lock: several agent turns can land at once and
# there is no reason to mint a token per request.
_TOKEN_MARGIN_SECONDS = 300
_token_lock = threading.Lock()
_token_cache: dict = {"value": None, "expires_at": 0.0}


class Community(BaseModel):
    name: str
    members: int
    description: str
    link: str


def _credentials() -> tuple[str, str] | None:
    """Configured client id/secret, treating blanks as absent."""
    cfg = settings()
    client_id = (cfg.reddit_client_id or "").strip()
    secret = (cfg.reddit_client_secret or "").strip()
    return (client_id, secret) if client_id and secret else None


def reset_token_cache() -> None:
    """Drop the cached token. For tests, and for credential rotation."""
    with _token_lock:
        _token_cache["value"] = None
        _token_cache["expires_at"] = 0.0


def _access_token() -> str | None:
    """App-only bearer token, cached until shortly before it expires.

    Returns None when credentials are absent OR when Reddit refuses — the
    caller then falls back to the keyless endpoint rather than failing the
    turn outright.
    """
    creds = _credentials()
    if creds is None:
        return None

    now = time.monotonic()
    with _token_lock:
        if _token_cache["value"] and now < _token_cache["expires_at"]:
            return _token_cache["value"]

        cfg = settings()
        try:
            resp = httpx.post(
                _TOKEN_URL,
                auth=creds,
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": cfg.reddit_user_agent},
                timeout=cfg.tool_timeout_seconds,
            ).raise_for_status().json()
        except (httpx.HTTPError, ValueError):
            return None

        token = resp.get("access_token")
        if not token:
            return None
        expires_in = float(resp.get("expires_in") or 3600)
        _token_cache["value"] = token
        _token_cache["expires_at"] = now + max(60.0, expires_in - _TOKEN_MARGIN_SECONDS)
        return token


def _to_communities(payload: dict) -> list[dict]:
    out = []
    for child in payload.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("subscribers") is None:
            continue
        out.append(Community(
            name=f"r/{d.get('display_name', '')}",
            members=int(d.get("subscribers", 0)),
            description=(d.get("public_description") or "")[:200],
            link=f"https://reddit.com{d.get('url', '')}",
        ).model_dump())
    return sorted(out, key=lambda c: -c["members"])


@tool
def search_communities(interest: str, max_results: int = 5) -> dict:
    """Find online communities (subreddits) around an interest — good for
    finding like-minded people, local chapters, and beginner advice.
    Returns {'communities': [...]} with member counts and links.
    If the result has an 'error' key, community search is down: say so.
    Prefer communities with more members and a real description.
    Present each as a markdown link — [r/climbing](link) — never a bare URL."""
    cfg = settings()
    token = _access_token()
    if token:
        url = _OAUTH_URL
        headers = {"Authorization": f"bearer {token}",
                   "User-Agent": cfg.reddit_user_agent}
    else:
        url = _KEYLESS_URL
        headers = {"User-Agent": cfg.reddit_user_agent}

    try:
        resp = httpx.get(
            url, timeout=cfg.tool_timeout_seconds,
            params={"q": interest, "limit": max_results},
            headers=headers,
        ).raise_for_status().json()
        return {"communities": _to_communities(resp)}
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return {"error": f"community search unavailable ({type(exc).__name__})",
                "interest": interest}
