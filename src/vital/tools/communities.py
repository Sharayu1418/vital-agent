"""Communities tool — Reddit public search adapter (D6).

Reddit's unauthenticated JSON endpoint is rate-limited but keyless — right
tradeoff for Phase 3. Meetup has no open API; Discord directories need
scraping. Both can become additional providers inside this same adapter
later without touching the agent.
"""
import httpx
from langchain_core.tools import tool
from pydantic import BaseModel

from vital.config import settings

_URL = "https://www.reddit.com/subreddits/search.json"


class Community(BaseModel):
    name: str
    members: int
    description: str
    link: str


@tool
def search_communities(interest: str, max_results: int = 5) -> dict:
    """Find online communities (subreddits) around an interest — good for
    finding like-minded people, local chapters, and beginner advice.
    Returns {'communities': [...]} with member counts and links.
    If the result has an 'error' key, community search is down: say so.
    Prefer communities with more members and a real description."""
    cfg = settings()
    try:
        resp = httpx.get(
            _URL, timeout=cfg.tool_timeout_seconds,
            params={"q": interest, "limit": max_results},
            headers={"User-Agent": "vital-app/0.3 (personal wellness copilot)"},
        ).raise_for_status().json()
        out = []
        for child in resp.get("data", {}).get("children", []):
            d = child.get("data", {})
            if d.get("subscribers") is None:
                continue
            out.append(Community(
                name=f"r/{d.get('display_name', '')}",
                members=int(d.get("subscribers", 0)),
                description=(d.get("public_description") or "")[:200],
                link=f"https://reddit.com{d.get('url', '')}",
            ).model_dump())
        return {"communities": sorted(out, key=lambda c: -c["members"])}
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return {"error": f"community search unavailable ({type(exc).__name__})",
                "interest": interest}
