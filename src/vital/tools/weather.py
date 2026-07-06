"""Weather tool — thin adapter over OpenWeather (design decision D6).

The agent sees OUR schema. OpenWeather is an implementation detail:
swap providers or add caching here without touching prompts.
"""
import httpx
from langchain_core.tools import tool
from pydantic import BaseModel

from vital.config import settings

_BASE = "https://api.openweathermap.org/data/2.5"


class WeatherReport(BaseModel):
    city: str
    temp_c: float
    feels_like_c: float
    condition: str          # e.g. "light rain", "clear sky"
    precip_prob_next_12h: float  # 0.0–1.0, max over next 12h
    outdoor_friendly: bool  # our own judgment, precomputed for the model


@tool
def get_weather(city: str) -> dict:
    """Get current weather and 12-hour precipitation outlook for a city.

    ALWAYS call this before recommending any outdoor activity.
    `outdoor_friendly` is a pre-computed hint: false means prefer indoor options.
    """
    cfg = settings()
    with httpx.Client(timeout=cfg.tool_timeout_seconds) as client:
        now = client.get(
            f"{_BASE}/weather",
            params={"q": city, "appid": cfg.openweather_api_key, "units": "metric"},
        ).raise_for_status().json()

        forecast = client.get(
            f"{_BASE}/forecast",
            params={"q": city, "appid": cfg.openweather_api_key, "units": "metric", "cnt": 4},
        ).raise_for_status().json()

    precip = max((slot.get("pop", 0.0) for slot in forecast.get("list", [])), default=0.0)
    temp = now["main"]["temp"]
    report = WeatherReport(
        city=city,
        temp_c=temp,
        feels_like_c=now["main"]["feels_like"],
        condition=now["weather"][0]["description"],
        precip_prob_next_12h=precip,
        outdoor_friendly=(precip < 0.4 and -5 <= temp <= 35),
    )
    return report.model_dump()
