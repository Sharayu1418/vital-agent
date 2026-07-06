"""Places tool — thin adapter over Google Places Text Search (D6)."""
import httpx
from langchain_core.tools import tool
from pydantic import BaseModel

from vital.config import settings

_URL = "https://places.googleapis.com/v1/places:searchText"


class Venue(BaseModel):
    name: str
    rating: float | None
    address: str
    maps_url: str
    price_level: str | None  # "FREE" .. "VERY_EXPENSIVE"


@tool
def search_places(query: str, city: str, max_results: int = 5) -> list[dict]:
    """Search for real venues and activity spots.

    Use specific activity queries, not generic ones:
    GOOD: 'bouldering gym', 'pottery class', 'hiking trail', 'board game cafe'
    BAD: 'fun things', 'activities'
    Returns real venues with ratings and Google Maps links. Include the
    maps_url in your recommendation so the user can act on it immediately.
    """
    cfg = settings()
    resp = httpx.post(
        _URL,
        timeout=cfg.tool_timeout_seconds,
        headers={
            "X-Goog-Api-Key": cfg.google_places_api_key,
            "X-Goog-FieldMask": ",".join([
                "places.displayName", "places.rating", "places.formattedAddress",
                "places.googleMapsUri", "places.priceLevel",
            ]),
        },
        json={"textQuery": f"{query} in {city}", "maxResultCount": max_results},
    ).raise_for_status().json()

    return [
        Venue(
            name=p["displayName"]["text"],
            rating=p.get("rating"),
            address=p.get("formattedAddress", ""),
            maps_url=p.get("googleMapsUri", ""),
            price_level=p.get("priceLevel", "").removeprefix("PRICE_LEVEL_") or None,
        ).model_dump()
        for p in resp.get("places", [])
    ]
