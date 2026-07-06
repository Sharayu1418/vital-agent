"""Tool adapter tests with mocked HTTP (respx) — no API keys needed.

These matter beyond correctness: recorded/mocked responses are the seed of
the deterministic eval fixtures in Phase 4 (design decision D6/D12).
"""
import httpx
import pytest
import respx

# Provide fake settings before importing tools
import os
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

from vital.tools.weather import get_weather  # noqa: E402
from vital.tools.places import search_places  # noqa: E402


@respx.mock
def test_weather_rainy_day_flags_indoor():
    respx.get(url__regex=r".*\/weather.*").mock(return_value=httpx.Response(200, json={
        "main": {"temp": 18.0, "feels_like": 17.0},
        "weather": [{"description": "light rain"}],
    }))
    respx.get(url__regex=r".*\/forecast.*").mock(return_value=httpx.Response(200, json={
        "list": [{"pop": 0.8}, {"pop": 0.6}],
    }))

    out = get_weather.invoke({"city": "Brooklyn"})
    assert out["precip_prob_next_12h"] == 0.8
    assert out["outdoor_friendly"] is False


@respx.mock
def test_places_returns_our_schema_not_googles():
    respx.post(url__regex=r".*searchText.*").mock(return_value=httpx.Response(200, json={
        "places": [{
            "displayName": {"text": "Brooklyn Boulders"},
            "rating": 4.6,
            "formattedAddress": "575 Degraw St, Brooklyn, NY",
            "googleMapsUri": "https://maps.google.com/?cid=123",
            "priceLevel": "PRICE_LEVEL_MODERATE",
        }],
    }))

    out = search_places.invoke({"query": "bouldering gym", "city": "Brooklyn"})
    assert out[0]["name"] == "Brooklyn Boulders"
    assert out[0]["price_level"] == "MODERATE"
    assert set(out[0]) == {"name", "rating", "address", "maps_url", "price_level"}


def test_tool_docstrings_are_llm_ready():
    # Docstrings ARE the tool spec the model sees — guard against lazy edits
    for t in (get_weather, search_places):
        assert len(t.description) > 60, f"{t.name}: description too thin for reliable tool use"
