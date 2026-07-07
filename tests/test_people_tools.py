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
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
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
