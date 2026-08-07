"""Places tool — thin adapter over Google Places Text Search (D6).

Failure policy: returns {"error": ...} instead of raising, so a Places
outage degrades the answer instead of killing the conversation.
"""
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


def search_near(query: str, lat: float, lng: float, radius_km: float = 10.0,
                max_results: int = 12) -> dict:
    """Venues near a coordinate, WITH their own coordinates.

    Not a tool — no model calls this. It backs the buddy meeting-point
    document, which has to compute distance from two people and therefore
    needs each venue's position, something the text search above does not
    return.

    Asks for more results than are shown: ranking three fair options out of
    twelve candidates is a decision, ranking three out of three is not.
    """
    cfg = settings()
    try:
        resp = httpx.post(
            _URL,
            timeout=cfg.tool_timeout_seconds,
            headers={
                "X-Goog-Api-Key": cfg.google_places_api_key,
                "X-Goog-FieldMask": ",".join([
                    "places.displayName", "places.rating",
                    "places.formattedAddress", "places.googleMapsUri",
                    "places.priceLevel", "places.location",
                ]),
            },
            json={
                "textQuery": query,
                "maxResultCount": max_results,
                # A bias, not a hard restriction: a genuinely better venue
                # just outside the circle should still be considered, and
                # meetup.py drops anything actually unreasonable.
                "locationBias": {"circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": min(max(radius_km, 1.0), 50.0) * 1000,
                }},
            },
        ).raise_for_status().json()

        out = []
        for place in resp.get("places", []):
            position = place.get("location") or {}
            if not (position.get("latitude") and position.get("longitude")):
                continue          # unplaceable venue cannot be ranked
            out.append({
                "name": place["displayName"]["text"],
                "address": place.get("formattedAddress", ""),
                "lat": float(position["latitude"]),
                "lng": float(position["longitude"]),
                "rating": place.get("rating"),
                "price_level": (place.get("priceLevel", "")
                                .removeprefix("PRICE_LEVEL_") or None),
                "maps_url": place.get("googleMapsUri", ""),
            })
        return {"venues": out}
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        return {"error": f"venue search unavailable ({type(exc).__name__})",
                "query": query}


@tool
def search_places(query: str, city: str, max_results: int = 5) -> dict:
    """Search for real venues and activity spots.

    Use specific activity queries, not generic ones:
    GOOD: 'bouldering gym', 'pottery class', 'hiking trail', 'board game cafe'
    BAD: 'fun things', 'activities'
    Returns {'venues': [...]} with ratings and Google Maps links. Render each
    one as a MARKDOWN LINK on the venue name — [The Court Club](maps_url) —
    never as a bare URL: these are long ?cid= links that wrap over several
    lines and make the answer unreadable.
    If the result contains an 'error' key, live venue search is down: say so,
    and give best-effort general suggestions clearly marked as unverified.
    """
    cfg = settings()
    try:
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

        venues = [
            Venue(
                name=p["displayName"]["text"],
                rating=p.get("rating"),
                address=p.get("formattedAddress", ""),
                maps_url=p.get("googleMapsUri", ""),
                price_level=p.get("priceLevel", "").removeprefix("PRICE_LEVEL_") or None,
            ).model_dump()
            for p in resp.get("places", [])
        ]
        return {"venues": venues}
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return {"error": f"venue search unavailable ({type(exc).__name__})", "query": query}
