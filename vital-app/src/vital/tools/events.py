"""Events tool — Ticketmaster Discovery adapter (D6).

Why Ticketmaster: free API key, no OAuth dance. Eventbrite/Meetup locked
their search APIs down (phase doc pitfall) — don't burn a week there.
Same failure policy as every adapter: {'error': ...}, never an exception.
"""
import httpx
from langchain_core.tools import tool
from pydantic import BaseModel

from vital.config import settings
from vital.tools import where

_URL = "https://app.ticketmaster.com/discovery/v2/events.json"

# Events are sparser than cafes. A gig 30 km away is still "something on near
# me" in a way a coffee shop at that distance is not, so this is wider than
# the 10 km used for venues.
DEFAULT_RADIUS_KM = 40

# Ticketmaster wants geoPoint as a geohash. Precision 7 is a ~150 m box —
# far finer than a 40 km radius needs, and it means the outbound request
# carries a neighbourhood rather than a doorstep.
GEOHASH_PRECISION = 7

# Base32 as geohash defines it: no a, i, l or o, because they misread.
_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lat: float, lng: float, precision: int = GEOHASH_PRECISION) -> str:
    """Encode a coordinate as a geohash.

    Interleaves longitude and latitude bits, each one halving the remaining
    range, then packs them five at a time into base32. A shorter geohash is
    a prefix of a longer one and names a bigger box containing it.

    The comparison is `>=`, not `>`. They agree everywhere except exactly on
    a midpoint, which sounds like a rounding detail right up until the input
    is (0, 0) — the prime meridian at the equator sits on every midpoint at
    once. `>` encodes it as "7zzzzz", the diagonally opposite corner of the
    world, and no other test case would have shown it.
    """
    lat_range, lng_range = [-90.0, 90.0], [-180.0, 180.0]
    out, bits, bit_count, even = [], 0, 0, True
    while len(out) < precision:
        rng, value = (lng_range, lng) if even else (lat_range, lat)
        mid = (rng[0] + rng[1]) / 2
        if value >= mid:
            bits = (bits << 1) | 1
            rng[0] = mid
        else:
            bits <<= 1
            rng[1] = mid
        even = not even
        bit_count += 1
        if bit_count == 5:
            out.append(_B32[bits])
            bits = bit_count = 0
    return "".join(out)


class Event(BaseModel):
    name: str
    date: str
    venue: str
    url: str


@tool
def search_events(interest: str, city: str | None = None,
                  max_results: int = 5) -> dict:
    """Find real upcoming local events for an interest (concerts, classes,
    meetups, games).

    OMIT `city` to search around the user — the server holds their exact
    coordinates and searches from there. Pass `city` ONLY for a different
    place they asked about.

    Returns {'events': [...]} with dates, venues and ticket
    links — include the url so the user can act immediately.
    If the result has an 'error' key, live event search is down: say so and
    suggest checking local listings, clearly marked as unverified."""
    cfg = settings()
    if not cfg.ticketmaster_api_key:
        return {"error": "events provider not configured", "interest": interest}

    place = where.resolve(city)
    if not place.known:
        return {"error": "no location available — ask the user where they are",
                "interest": interest}

    # A geohash plus an explicit radius, or a name. `radius` without `unit`
    # is interpreted as MILES, which would quietly make every search 1.6x
    # wider than intended.
    locator = ({"geoPoint": geohash(place.lat, place.lng),
                "radius": DEFAULT_RADIUS_KM, "unit": "km"} if place.precise
               else {"city": place.name})

    try:
        resp = httpx.get(_URL, timeout=cfg.tool_timeout_seconds, params={
            "apikey": cfg.ticketmaster_api_key, "keyword": interest,
            "size": max_results, "sort": "date,asc", **locator,
        }).raise_for_status().json()
        raw = resp.get("_embedded", {}).get("events", [])
        events = [Event(
            name=e["name"],
            date=e.get("dates", {}).get("start", {}).get("localDate", ""),
            venue=(e.get("_embedded", {}).get("venues") or [{}])[0].get("name", ""),
            url=e.get("url", ""),
        ).model_dump() for e in raw]
        return {"events": events}
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return {"error": f"event search unavailable ({type(exc).__name__})",
                "interest": interest}
