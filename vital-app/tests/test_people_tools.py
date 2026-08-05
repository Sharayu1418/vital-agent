"""Events + communities adapter tests (respx, no keys/network)."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import httpx
import pytest
import respx

from vital.tools.communities import search_communities
from vital.tools.events import search_events


@pytest.fixture(autouse=True)
def fresh_settings():
    from vital.config import settings
    settings.cache_clear()
    yield
    settings.cache_clear()


def test_events_without_key_reports_not_configured(monkeypatch):
    # blank, not delenv — deleting would let pydantic read a dev's real .env
    monkeypatch.setenv("TICKETMASTER_API_KEY", "")
    out = search_events.invoke({"interest": "pottery", "city": "Brooklyn"})
    assert "error" in out and "not configured" in out["error"]


@respx.mock
def test_events_maps_to_our_schema(monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "tm-key")
    from vital.config import settings
    settings.cache_clear()
    respx.get(url__regex=r".*ticketmaster.*").mock(return_value=httpx.Response(200, json={
        "_embedded": {"events": [{
            "name": "Wheel Throwing Workshop",
            "dates": {"start": {"localDate": "2026-07-11"}},
            "_embedded": {"venues": [{"name": "BKLYN Clay"}]},
            "url": "https://tm.example/evt1",
        }]},
    }))
    out = search_events.invoke({"interest": "pottery", "city": "Brooklyn"})
    assert out["events"][0] == {"name": "Wheel Throwing Workshop", "date": "2026-07-11",
                                "venue": "BKLYN Clay", "url": "https://tm.example/evt1"}


@respx.mock
def test_events_provider_error_degrades(monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "tm-key")
    from vital.config import settings
    settings.cache_clear()
    respx.get(url__regex=r".*ticketmaster.*").mock(return_value=httpx.Response(500))
    out = search_events.invoke({"interest": "pottery", "city": "Brooklyn"})
    assert "error" in out


@respx.mock
def test_communities_sorted_by_members():
    respx.get(url__regex=r".*reddit.*").mock(return_value=httpx.Response(200, json={
        "data": {"children": [
            {"data": {"display_name": "Pottery", "subscribers": 500_000,
                      "public_description": "All things ceramic", "url": "/r/Pottery/"}},
            {"data": {"display_name": "nycCeramics", "subscribers": 8_000,
                      "public_description": "NYC studios and firings", "url": "/r/nycCeramics/"}},
            {"data": {"display_name": "brandnew", "subscribers": None}},  # skipped
        ]},
    }))
    out = search_communities.invoke({"interest": "pottery"})
    names = [c["name"] for c in out["communities"]]
    assert names == ["r/Pottery", "r/nycCeramics"]
    assert out["communities"][0]["link"] == "https://reddit.com/r/Pottery/"


@respx.mock
def test_communities_rate_limited_degrades():
    respx.get(url__regex=r".*reddit.*").mock(return_value=httpx.Response(429))
    out = search_communities.invoke({"interest": "pottery"})
    assert "error" in out


# ---------- Reddit app-only OAuth ----------

SUBREDDIT_PAYLOAD = {
    "data": {"children": [
        {"data": {"display_name": "climbing", "subscribers": 400_000,
                  "public_description": "Rock climbing", "url": "/r/climbing/"}},
    ]}
}


@pytest.fixture(autouse=True)
def clear_reddit_token():
    from vital.tools import communities
    communities.reset_token_cache()
    yield
    communities.reset_token_cache()


def _with_creds(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    from vital.config import settings
    settings.cache_clear()


@respx.mock
def test_without_credentials_uses_the_keyless_endpoint(monkeypatch):
    """Local dev must keep working with no secrets configured."""
    monkeypatch.setenv("REDDIT_CLIENT_ID", "")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "")
    from vital.config import settings
    settings.cache_clear()

    keyless = respx.get(url__regex=r".*www\.reddit\.com/subreddits/search\.json.*").mock(
        return_value=httpx.Response(200, json=SUBREDDIT_PAYLOAD))
    token = respx.post(url__regex=r".*access_token.*")

    out = search_communities.invoke({"interest": "climbing"})
    assert out["communities"][0]["name"] == "r/climbing"
    assert keyless.called
    assert not token.called, "must not try to mint a token without credentials"


@respx.mock
def test_with_credentials_authenticates_and_uses_the_oauth_host(monkeypatch):
    """The whole point: Reddit blocks datacenter IPs on the keyless host, so
    production must go through oauth.reddit.com with a bearer token."""
    _with_creds(monkeypatch)
    token = respx.post(url__regex=r".*access_token.*").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1",
                                               "expires_in": 86400}))
    oauth = respx.get(url__regex=r".*oauth\.reddit\.com/subreddits/search.*").mock(
        return_value=httpx.Response(200, json=SUBREDDIT_PAYLOAD))

    out = search_communities.invoke({"interest": "climbing"})
    assert out["communities"][0]["members"] == 400_000
    assert token.called and oauth.called
    assert oauth.calls[0].request.headers["authorization"] == "bearer tok-1"
    assert "vital-app" in oauth.calls[0].request.headers["user-agent"]


@respx.mock
def test_token_is_cached_across_calls(monkeypatch):
    """One token per ~24h, not one per agent turn."""
    _with_creds(monkeypatch)
    token = respx.post(url__regex=r".*access_token.*").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1",
                                               "expires_in": 86400}))
    respx.get(url__regex=r".*oauth\.reddit\.com.*").mock(
        return_value=httpx.Response(200, json=SUBREDDIT_PAYLOAD))

    for _ in range(3):
        search_communities.invoke({"interest": "climbing"})
    assert token.call_count == 1


@respx.mock
def test_a_token_that_expires_immediately_is_not_cached_forever(monkeypatch):
    """expires_in is clamped to a sane floor rather than trusted blindly."""
    _with_creds(monkeypatch)
    from vital.tools import communities
    respx.post(url__regex=r".*access_token.*").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1",
                                               "expires_in": 0}))
    respx.get(url__regex=r".*oauth\.reddit\.com.*").mock(
        return_value=httpx.Response(200, json=SUBREDDIT_PAYLOAD))

    search_communities.invoke({"interest": "climbing"})
    assert communities._token_cache["expires_at"] > 0


@respx.mock
def test_token_failure_degrades_to_keyless_rather_than_failing_the_turn(monkeypatch):
    _with_creds(monkeypatch)
    respx.post(url__regex=r".*access_token.*").mock(
        return_value=httpx.Response(401, json={"error": "invalid_grant"}))
    keyless = respx.get(url__regex=r".*www\.reddit\.com/subreddits/search\.json.*").mock(
        return_value=httpx.Response(200, json=SUBREDDIT_PAYLOAD))

    out = search_communities.invoke({"interest": "climbing"})
    assert "communities" in out, "bad credentials must not kill the turn"
    assert keyless.called


@respx.mock
def test_search_failure_still_degrades_to_an_error_key(monkeypatch):
    """D6 policy unchanged: the agent gets a clean signal, never an exception."""
    _with_creds(monkeypatch)
    respx.post(url__regex=r".*access_token.*").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 900}))
    respx.get(url__regex=r".*oauth\.reddit\.com.*").mock(
        return_value=httpx.Response(503))

    out = search_communities.invoke({"interest": "climbing"})
    assert "error" in out and out["interest"] == "climbing"
