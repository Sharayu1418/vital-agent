"""CORS: the frontend and the backend must agree on request headers.

WHY THIS FILE EXISTS
--------------------
`allow_headers` is an explicit allowlist. Adding a header on the client
without adding it here makes the browser fail the PREFLIGHT for every
call — so the entire app reports "can't reach the backend" while the
server is healthy, answering curl normally, and logging nothing wrong.

That is what happened when X-UTC-Offset was added to lib/api.js: every
endpoint broke at once, the failure looked like an outage, and the only
symptom was in the browser's network tab.

So this test reads the ACTUAL frontend client and asserts every header it
sends is allowed. It fails on the next header too, not just that one —
the point is to pin the contract, not the incident.
"""
import os
import pathlib
import re

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

API_CLIENT = (pathlib.Path(__file__).resolve().parents[2]
              / "vital-web" / "app" / "lib" / "api.js")


def _cors_middleware():
    import vital.api as api

    for middleware in api.app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            return middleware.kwargs
    pytest.fail("no CORSMiddleware installed — every browser call would fail")


def _allowed_headers() -> set[str]:
    return {h.lower() for h in _cors_middleware().get("allow_headers", [])}


def _headers_the_frontend_sends() -> set[str]:
    """Every header name lib/api.js attaches to a request.

    Covers the three shapes actually used there:
        headers.Authorization = ...
        headers["X-UTC-Offset"] = ...
        const json = { "Content-Type": ... }
    """
    if not API_CLIENT.exists():
        pytest.skip("frontend not checked out alongside the backend")
    source = API_CLIENT.read_text()

    names = set(re.findall(r'headers\[\s*["\']([\w-]+)["\']\s*\]\s*=', source))
    names |= set(re.findall(r'headers\.([A-Za-z][\w-]*)\s*=', source))
    names |= set(re.findall(r'["\']([\w-]+)["\']\s*:\s*["\']application/', source))
    return {n.lower() for n in names}


def test_every_header_the_frontend_sends_is_allowed():
    sending = _headers_the_frontend_sends()
    allowed = _allowed_headers()
    missing = sending - allowed
    assert not missing, (
        f"lib/api.js sends {sorted(missing)} but CORS allow_headers is "
        f"{sorted(allowed)}. The browser will fail the preflight on EVERY "
        "request and the app will report that the backend is unreachable "
        "while the server is perfectly healthy.")


def test_the_scan_actually_finds_something():
    """Guards the guard. If the regexes stop matching — a refactor to a
    headers object, say — the test above would pass vacuously and the
    protection would be gone with no failure to notice."""
    found = _headers_the_frontend_sends()
    assert "authorization" in found, (
        "the header scan found no Authorization header, so it is no longer "
        "reading lib/api.js correctly and proves nothing")


def test_the_timezone_header_is_allowed():
    """Named explicitly because it is on the critical path: the server is
    UTC, and without this header every sleep log is filed under the wrong
    day and the energy forecast is anchored to the wrong clock."""
    assert "x-utc-offset" in _allowed_headers()


def test_credentials_are_allowed_but_origins_are_not_wildcarded():
    """allow_credentials with a wildcard origin would let any site make
    authenticated requests as your users. Browsers refuse that combination,
    but the check is cheap and the consequence is total."""
    kwargs = _cors_middleware()
    assert kwargs.get("allow_credentials") is True
    assert "*" not in (kwargs.get("allow_origins") or [])


def test_the_session_header_survives_for_mobile():
    """The mobile client cannot use cookies, so it carries the session in a
    header. Dropping it from either list silently breaks that client only."""
    assert "x-vital-session" in _allowed_headers()
    assert "X-Vital-Session" in (_cors_middleware().get("expose_headers") or [])
