"""Wearable sync: the Google Health adapter, the state parameter, the seam.

The sample payloads below are copied from Google's own endpoint
documentation rather than invented, because a test written against a shape
I imagined would confirm my assumption and nothing else. That is the
mistake this codebase has made four times: the harness and production
measuring different things.

Security-critical tests are grouped at the bottom. The state parameter is
what stops an attacker linking THEIR Fitbit account to YOUR VITAL account,
which would silently drive your forecast and every plan built on it.
"""
import os
from datetime import date, datetime, time

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import oauth_state, secrets as token_secrets
from vital.config import settings
from vital.providers.base import (ProviderAuthError, ProviderUnavailable,
                                  SleepProvider)
from vital.providers.google_health import GoogleHealthProvider

# A real Fitbit night, from developers.google.com/health/endpoints.
# 20:57:30Z to 04:41:30Z, 407 minutes asleep out of 464 in bed.
SLEEP_POINT = {
    "dataSource": {"recordingMethod": "DERIVED",
                   "device": {"displayName": "Charge 6"},
                   "platform": "FITBIT"},
    "sleep": {
        "interval": {"startTime": "2026-03-03T20:57:30Z", "startUtcOffset": "0s",
                     "endTime": "2026-03-04T04:41:30Z", "endUtcOffset": "0s"},
        "type": "STAGES",
        "metadata": {"stagesStatus": "SUCCEEDED", "processed": True, "main": True},
        "summary": {"minutesInSleepPeriod": "464", "minutesAsleep": "407",
                    "minutesAwake": "57"},
    },
}

NAP_POINT = {
    "sleep": {
        "interval": {"startTime": "2026-03-04T18:00:00Z", "startUtcOffset": "0s",
                     "endTime": "2026-03-04T19:00:00Z", "endUtcOffset": "0s"},
        "metadata": {"main": False},
        "summary": {"minutesInSleepPeriod": "60", "minutesAsleep": "55"},
    },
}


@pytest.fixture
def provider():
    return GoogleHealthProvider()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_HEALTH_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_HEALTH_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("GOOGLE_HEALTH_REDIRECT_URI",
                       "https://api.example.com/connect/google-health/callback")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY",
                       "8_3vHkkAqhY1qWZQ0KHmnGZbFJ8RaTNaRP2cHXLGJhE=")
    settings.cache_clear()
    token_secrets._cipher.cache_clear()
    yield
    settings.cache_clear()
    token_secrets._cipher.cache_clear()


# ---------- parsing what Google actually returns ----------

def test_a_documented_sleep_point_becomes_a_night(provider):
    night = provider._to_night(SLEEP_POINT)
    assert night is not None
    assert night.date == "2026-03-04"          # filed under the waking morning
    assert night.duration_min == 407           # minutesAsleep, not 464 in bed
    assert night.bedtime == time(20, 57)
    assert night.wake_time == time(4, 41)


def test_time_in_bed_is_not_used_as_sleep(provider):
    """minutesInSleepPeriod includes time lying awake. Using it would
    understate sleep debt for exactly the people who have the most."""
    assert provider._to_night(SLEEP_POINT).duration_min != 464


def test_naps_are_not_nights(provider):
    """metadata.main separates the night from a nap. A nap stored as a
    night moves the wake-time average the whole forecast is keyed to."""
    assert provider._to_night(NAP_POINT) is None


def test_the_local_offset_is_applied(provider):
    """startUtcOffset travels with each interval, which is the whole reason
    this provider improves the forecast: real local clock times instead of
    a population default."""
    shifted = {"sleep": {**SLEEP_POINT["sleep"],
                         "interval": {"startTime": "2026-03-03T20:57:30Z",
                                      "startUtcOffset": "-18000s",   # UTC-5
                                      "endTime": "2026-03-04T04:41:30Z",
                                      "endUtcOffset": "-18000s"}}}
    night = provider._to_night(shifted)
    assert night.bedtime == time(15, 57)
    assert night.wake_time == time(23, 41)
    assert night.date == "2026-03-03"


def test_string_encoded_integers_are_parsed(provider):
    """Google serialises int64 as JSON strings. Trusting the type would
    make duration a string and every debt calculation downstream fail."""
    assert isinstance(provider._to_night(SLEEP_POINT).duration_min, int)


def test_implausible_durations_are_dropped(provider):
    for minutes in ("5", "1500"):
        point = {"sleep": {**SLEEP_POINT["sleep"],
                           "summary": {"minutesAsleep": minutes,
                                       "minutesInSleepPeriod": minutes}}}
        assert provider._to_night(point) is None


def test_malformed_points_return_none_rather_than_raising(provider):
    """One bad record must not fail a whole sync."""
    for bad in [{}, {"sleep": {}}, {"sleep": {"metadata": {"main": True}}},
                {"sleep": {"metadata": {"main": True},
                           "interval": {"startTime": "not-a-time",
                                        "endTime": "also-not"}}}]:
        assert provider._to_night(bad) is None


def test_quality_comes_from_efficiency(provider):
    """407 of 464 minutes is 88% efficiency -> 3. Crude on purpose: Fitbit's
    own sleep score is not in this payload, and deriving something
    sophisticated-looking from stage data would imply precision this
    mapping does not have."""
    assert provider._to_night(SLEEP_POINT).quality == 3


# ---------- pagination and failure handling ----------

def test_it_paginates_because_sleep_pages_cap_at_25(provider, monkeypatch):
    """25 is the documented default AND maximum for sleep. Without
    following nextPageToken, any history request silently truncates."""
    pages = [
        {"dataPoints": [SLEEP_POINT], "nextPageToken": "page-2"},
        {"dataPoints": [{"sleep": {**SLEEP_POINT["sleep"],
                                   "interval": {"startTime": "2026-03-04T21:00:00Z",
                                                "startUtcOffset": "0s",
                                                "endTime": "2026-03-05T05:00:00Z",
                                                "endUtcOffset": "0s"}}}],
         "nextPageToken": ""},
    ]
    calls = []

    def fake_get(url, params, headers):
        calls.append(params.get("pageToken"))
        return pages[len(calls) - 1]

    monkeypatch.setattr(provider, "_get", fake_get)
    nights = provider.fetch_nights("token", date(2026, 3, 1))
    assert len(nights) == 2
    assert calls == [None, "page-2"]


def test_an_expired_refresh_token_is_an_auth_error(provider, configured, monkeypatch):
    """The one that matters most: in Testing publishing status Google
    expires refresh tokens every 7 DAYS. This must reach the user as
    'reconnect', never as a retryable blip — that conflation is why the
    Reddit tool looked temporarily down for months."""
    import httpx

    class Resp:
        status_code = 400

        @staticmethod
        def json():
            return {"error": "invalid_grant"}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp())
    with pytest.raises(ProviderAuthError):
        provider.access_token("dead-token")


def test_server_errors_are_transient_not_auth(provider, configured, monkeypatch):
    import httpx

    class Resp:
        status_code = 503

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp())
    with pytest.raises(ProviderUnavailable):
        provider.access_token("token")


def test_a_missing_refresh_token_fails_loudly(provider, configured, monkeypatch):
    """Google omits the refresh token on re-authorization without
    prompt=consent. Storing just the access token would work for an hour
    and then break permanently."""
    import httpx

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "abc", "expires_in": 3600}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp())
    with pytest.raises(ProviderAuthError):
        provider.exchange_code("code")


def test_the_authorize_url_asks_for_a_durable_grant(provider, configured):
    url = provider.authorize_url("state-abc")
    assert "access_type=offline" in url      # without it: no refresh token
    assert "prompt=consent" in url           # without it: none on re-auth
    assert "googlehealth.sleep.readonly" in url
    assert "state=state-abc" in url


def test_it_requests_only_the_scope_it_needs(provider, configured):
    """Restricted scopes are reviewed one by one at verification, and each
    extra one is another thing to justify and another thing to leak."""
    url = provider.authorize_url("s")
    assert "activity" not in url and "heart" not in url


def test_it_asks_for_wearable_data_not_manual_entries(provider, monkeypatch):
    """The user may also be logging sleep in VITAL by hand. Pulling Google's
    manual entries too would double-count nights and hide real debt."""
    captured = {}

    def fake_get(url, params, headers):
        captured.update(params)
        return {"dataPoints": []}

    monkeypatch.setattr(provider, "_get", fake_get)
    provider.fetch_nights("token", date(2026, 3, 1))
    assert captured["dataSourceFamily"].endswith("google-wearables")
    assert "civil_end_time" in captured["filter"]


# ---------- the seam ----------

def test_google_health_satisfies_the_provider_protocol(provider):
    """If this fails, a second provider cannot be dropped in behind the
    same sync code — which is the entire justification for the seam."""
    assert isinstance(provider, SleepProvider)


def test_unknown_providers_are_rejected():
    from vital import providers
    with pytest.raises(KeyError):
        providers.get_provider("oura")     # not built: cannot be tested for real


# ---------- token encryption ----------

def test_refresh_tokens_round_trip_through_encryption(configured):
    ciphertext = token_secrets.encrypt("refresh-abc")
    assert "refresh-abc" not in ciphertext
    assert token_secrets.decrypt(ciphertext) == "refresh-abc"


def test_encryption_refuses_rather_than_falling_back_to_plaintext(monkeypatch):
    """A silent downgrade would write live bearer credentials to somebody's
    health data into the database in the clear, and nothing downstream
    would look different."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    settings.cache_clear()
    token_secrets._cipher.cache_clear()
    try:
        with pytest.raises(token_secrets.EncryptionUnavailable):
            token_secrets.encrypt("refresh-abc")
        assert token_secrets.available() is False
    finally:
        settings.cache_clear()
        token_secrets._cipher.cache_clear()


def test_a_rotated_key_reads_as_reconnect_not_corruption(configured):
    ciphertext = token_secrets.encrypt("refresh-abc")
    token_secrets._cipher.cache_clear()
    os.environ["TOKEN_ENCRYPTION_KEY"] = "Zt8n5rQ9vKcH2mXpL4yJwF7sN1bT6aD3eG0iU5oR9cM="
    settings.cache_clear()
    try:
        with pytest.raises(token_secrets.EncryptionUnavailable):
            token_secrets.decrypt(ciphertext)
    finally:
        settings.cache_clear()
        token_secrets._cipher.cache_clear()


# ---------- the state parameter: login CSRF ----------

def test_a_state_verifies_for_the_session_that_issued_it(configured):
    state = oauth_state.issue("session-abc", "google-health")
    oauth_state.verify(state, "session-abc", "google-health")   # no raise


def test_a_state_from_another_session_is_rejected(configured):
    """THE attack. Without this an attacker completes consent with their
    own Fitbit, feeds the code to your browser, and their sleep data drives
    your forecast and every plan built on it."""
    state = oauth_state.issue("attacker-session", "google-health")
    with pytest.raises(oauth_state.StateError):
        oauth_state.verify(state, "victim-session", "google-health")


def test_a_tampered_state_is_rejected(configured):
    state = oauth_state.issue("session-abc", "google-health")
    body, _, signature = state.partition(".")
    forged = oauth_state.issue("attacker", "google-health").partition(".")[0]
    with pytest.raises(oauth_state.StateError):
        oauth_state.verify(f"{forged}.{signature}", "session-abc", "google-health")


def test_an_unsigned_state_is_rejected(configured):
    import base64, json
    payload = {"s": "x" * 32, "p": "google-health", "e": 9_999_999_999, "n": "n"}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with pytest.raises(oauth_state.StateError):
        oauth_state.verify(f"{body}.", "session-abc", "google-health")


def test_an_expired_state_is_rejected(configured, monkeypatch):
    state = oauth_state.issue("session-abc", "google-health")
    monkeypatch.setattr(oauth_state.time, "time",
                        lambda: 9_999_999_999)
    with pytest.raises(oauth_state.StateError):
        oauth_state.verify(state, "session-abc", "google-health")


def test_a_state_for_another_provider_is_rejected(configured):
    state = oauth_state.issue("session-abc", "google-health")
    with pytest.raises(oauth_state.StateError):
        oauth_state.verify(state, "session-abc", "oura")


def test_empty_and_garbage_states_are_rejected(configured):
    for bad in ["", "no-dot", "a.b", "....", "%%%.%%%"]:
        with pytest.raises(oauth_state.StateError):
            oauth_state.verify(bad, "session-abc", "google-health")
