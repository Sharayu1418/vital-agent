"""Events adapter tests (respx, no keys/network).

Community search no longer has a third-party provider — see
docs/LIMITATIONS.md. People Connector coverage lives in
test_people_connector.py.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import httpx
import pytest
import respx

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
