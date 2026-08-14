"""The wearable sync diagnostic.

"The forecast says 10% confidence" has at least seven possible causes and
they are indistinguishable from outside: no server credentials, no
connection, an expired refresh token, an unreadable one, a provider
outage, an account with no data, or rows that arrived and never reached
the forecast.

Guessing between those is how an afternoon disappears. This walks the
chain in order and stops at the first break.

The most important test here is the last one: a diagnostic endpoint is an
easy place to build an information leak by accident.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import storage, sync


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_HEALTH_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_HEALTH_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("GOOGLE_HEALTH_REDIRECT_URI", "https://x/callback")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY",
                       "8_3vHkkAqhY1qWZQ0KHmnGZbFJ8RaTNaRP2cHXLGJhE=")
    from vital import secrets as token_secrets
    from vital.config import settings
    settings.cache_clear()
    token_secrets._cipher.cache_clear()
    yield
    settings.cache_clear()
    token_secrets._cipher.cache_clear()


def failed_step(result):
    return next((s for s in result["steps"] if not s["ok"]), None)


# ---------- it stops at the first real break ----------

def test_unconfigured_server_is_named_first(monkeypatch):
    """Before anything else: if the deployment has no credentials, every
    later step would fail for a misleading reason."""
    monkeypatch.setenv("GOOGLE_HEALTH_CLIENT_ID", "")
    from vital.config import settings
    settings.cache_clear()
    try:
        result = sync.diagnose("u1")
        assert failed_step(result)["step"] == "server credentials"
        assert "configured" in result["summary"].lower()
    finally:
        settings.cache_clear()


def test_no_connection_is_reported_as_the_cause(configured):
    result = sync.diagnose("nobody-connected")
    step = failed_step(result)
    assert step["step"] == "connection"
    assert "Connect" in step["fix"]
    # And it stops there rather than reporting six further failures that
    # are all the same fact restated.
    assert not any(s["step"] == "access token" for s in result["steps"])


def test_an_expired_token_is_named_with_the_seven_day_reason(configured, monkeypatch):
    """The most common real failure. In Google's Testing publishing status
    refresh tokens expire weekly BY DESIGN, and the fix is one click — but
    only if somebody says so."""
    storage.save_connection("u-expired", "google-health", "dead-token")

    from vital.providers.base import ProviderAuthError
    from vital.providers.google_health import GoogleHealthProvider

    def boom(self, refresh_token):
        raise ProviderAuthError("Google rejected the saved authorization")

    monkeypatch.setattr(GoogleHealthProvider, "access_token", boom)
    result = sync.diagnose("u-expired")
    step = failed_step(result)
    assert step["step"] == "access token"
    assert "7 days" in step["fix"] or "expire" in step["fix"]


def test_a_reachable_provider_with_no_data_says_so(configured, monkeypatch):
    """Distinct from a broken connection, and the fix is completely
    different — check the watch actually synced."""
    storage.save_connection("u-empty", "google-health", "good-token")

    from vital.providers.google_health import GoogleHealthProvider
    monkeypatch.setattr(GoogleHealthProvider, "access_token",
                        lambda self, t: ("access", 3600))
    monkeypatch.setattr(GoogleHealthProvider, "fetch_nights",
                        lambda self, t, since: [])

    result = sync.diagnose("u-empty")
    step = next(s for s in result["steps"] if s["step"] == "provider data")
    assert not step["ok"]
    assert "watch" in step["fix"].lower()


def test_data_fetched_but_not_stored_is_distinguished(configured, monkeypatch):
    """Fetching and storing are separate steps, so they fail separately.
    Without this distinction the answer is 'something is wrong'."""
    from datetime import time

    from vital import forecast as engine
    from vital.providers.google_health import GoogleHealthProvider

    storage.save_connection("u-unsaved", "google-health", "good-token")
    monkeypatch.setattr(GoogleHealthProvider, "access_token",
                        lambda self, t: ("access", 3600))
    monkeypatch.setattr(GoogleHealthProvider, "fetch_nights",
                        lambda self, t, since: [engine.Night(
                            date="2026-08-06", duration_min=420,
                            wake_time=time(7, 0), bedtime=time(23, 0))])

    result = sync.diagnose("u-unsaved")
    step = next(s for s in result["steps"] if s["step"] == "stored locally")
    assert not step["ok"]
    assert "Sync now" in step["fix"]


def test_everything_working_but_thin_data_is_still_explained(configured, monkeypatch):
    """The case that actually applies most often: nothing is broken, there
    simply are not enough nights yet. That must not read as a fault."""
    from datetime import time

    from vital import forecast as engine
    from vital.providers.google_health import GoogleHealthProvider

    storage.save_connection("u-thin", "google-health", "good-token")
    storage.save_health_rows("u-thin", [
        {"date": "2026-08-06", "duration_min": 420, "quality": "4",
         "source": "google-health"}])
    monkeypatch.setattr(GoogleHealthProvider, "access_token",
                        lambda self, t: ("access", 3600))
    monkeypatch.setattr(GoogleHealthProvider, "fetch_nights",
                        lambda self, t, since: [engine.Night(
                            date="2026-08-06", duration_min=420,
                            wake_time=time(7, 0), bedtime=time(23, 0))])

    result = sync.diagnose("u-thin")
    step = next(s for s in result["steps"] if s["step"] == "forecast input")
    assert "14 nights" in step.get("fix", "") or "confidence" in step["detail"]


# ---------- the security property ----------

def test_the_trace_never_contains_a_token(configured, monkeypatch):
    """A diagnostic that helpfully prints what it found is how credentials
    end up in a screenshot in a support thread."""
    import json

    from vital.providers.google_health import GoogleHealthProvider

    storage.save_connection("u-secret", "google-health", "SUPER-SECRET-REFRESH")
    monkeypatch.setattr(GoogleHealthProvider, "access_token",
                        lambda self, t: ("SUPER-SECRET-ACCESS", 3600))
    monkeypatch.setattr(GoogleHealthProvider, "fetch_nights",
                        lambda self, t, since: [])

    body = json.dumps(sync.diagnose("u-secret"))
    assert "SUPER-SECRET-REFRESH" not in body
    assert "SUPER-SECRET-ACCESS" not in body


def test_it_only_describes_the_caller(configured, monkeypatch):
    """Scoped by user id, with no parameter that could name anyone else."""
    import inspect

    signature = inspect.signature(sync.diagnose)
    assert list(signature.parameters) == ["user_id", "provider_name"]

    storage.save_connection("someone-else", "google-health", "their-token")
    from vital.providers.google_health import GoogleHealthProvider
    monkeypatch.setattr(GoogleHealthProvider, "access_token",
                        lambda self, t: ("a", 3600))
    result = sync.diagnose("a-different-user")
    assert failed_step(result)["step"] == "connection"


def test_the_endpoint_requires_identity_and_is_rate_limited():
    """It makes a live provider call, so an unauthenticated or unlimited
    version would be a free way to burn somebody's API quota."""
    import inspect

    import vital.api as api

    source = inspect.getsource(api.connect_diagnose)
    assert 'ident.limit("connect")' in source
    assert "Identity = Depends()" in inspect.getsource(api.connect_diagnose)
