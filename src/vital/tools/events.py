"""Events tool — Ticketmaster Discovery adapter (D6).

Why Ticketmaster: free API key, no OAuth dance. Eventbrite/Meetup locked
their search APIs down (phase doc pitfall) — don't burn a week there.
Same failure policy as every adapter: {'error': ...}, never an exception.
"""
import httpx
from langchain_core.tools import tool
from pydantic import BaseModel

from vital.config import settings

_URL = "https://app.ticketmaster.com/discovery/v2/events.json"


class Event(BaseModel):
    name: str
    date: str
    venue: str
    url: str


@tool
def search_events(interest: str, city: str, max_results: int = 5) -> dict:
    """Find real upcoming local events for an interest (concerts, classes,
    meetups, games). Returns {'events': [...]} with dates, venues and ticket
    links — include the url so the user can act immediately.
    If the result has an 'error' key, live event search is down: say so and
    suggest checking local listings, clearly marked as unverified."""
    cfg = settings()
    if not cfg.ticketmaster_api_key:
        return {"error": "events provider not configured", "interest": interest}
    try:
        resp = httpx.get(_URL, timeout=cfg.tool_timeout_seconds, params={
            "apikey": cfg.ticketmaster_api_key, "keyword": interest,
            "city": city, "size": max_results, "sort": "date,asc",
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
