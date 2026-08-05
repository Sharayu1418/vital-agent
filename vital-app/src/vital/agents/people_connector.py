"""People Connector — turns accepted ideas/interests into humans to do
them with (Phase 3B).

No third-party community provider, deliberately. Reddit, Meetup, Facebook
Groups, Eventbrite search and Strava clubs all closed or went paid between
2019 and 2026; the Reddit integration sat dead in production for months
while degrading gracefully enough that nobody noticed. Community discovery
now runs on things we own or that are commoditised: the Activity Buddy
Board (ours), Google Places (where an activity actually happens), and
Ticketmaster (ticketed events). See docs/LIMITATIONS.md.

v2 (backlog): opt-in VITAL-user matching via pgvector interest embeddings.
"""
from langchain_core.tools import tool
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

from vital import buddies, storage
from vital.config import settings
from vital.tools.events import search_events
from vital.tools.places import search_places


@tool
def get_user_interests() -> list[str]:
    """The user's saved interests — check BEFORE searching so suggestions
    connect to what they already care about."""
    return storage.interests()


@tool
def find_activity_buddies(activity: str, city: str | None = None,
                          time_window: str | None = None,
                          skill_level: str | None = None,
                          budget: str | None = None) -> dict:
    """Search VITAL's opt-in Activity Buddy Board for real users who posted
    that they want company for a similar activity. Returns {'matches': [...]}
    with display names, approximate city/area, vibe, time window, and match
    reasons — never exact locations or contact details. If 'matches' is
    empty, suggest the user create a buddy post. If the result has an
    'error' key, buddy search is temporarily unavailable: say so.
    Identity is resolved server-side; results already exclude the user's
    own posts."""
    try:
        posts = buddies.search_posts(
            storage.current_user_id.get(), activity=activity, city=city,
            time_window=time_window, skill_level=skill_level, budget=budget,
            limit=5)
    except Exception as exc:  # storage failure must degrade, not crash the turn
        return {"error": f"buddy search unavailable ({type(exc).__name__})"}
    return {"matches": posts, "count": len(posts), "safety_note": buddies.SAFETY_NOTE}


SYSTEM_PROMPT = """You are VITAL's People Connector. The user wants to find \
people, groups, or places to share an interest with.

Work in this order — most human first:
1. get_user_interests; if the message names an interest, use that.
2. find_activity_buddies — real VITAL users who opted in. This is the only
   source of actual people, so always try it. You need an activity and
   ideally a city; if the city is unknown, ask for their approximate
   city/area (never an address).
3. search_places — where this activity actually happens near them, because
   that is where its community is. Search the GATHERING PLACE, not a
   generic term: 'bouldering gym', 'run club', 'pottery studio',
   'community centre', 'chess cafe'. Aim for 2-3.
4. search_events — 1-2 upcoming ticketed events, if their city is known.

For EACH suggestion, one line on why it fits THIS user, tied to a stored
interest or something they said. Never generic.

Hard rules — what you must not do:
- NEVER suggest online communities, forums, subreddits, Discord servers,
  Facebook groups or apps from your own knowledge. You have no tool that
  can check whether they exist, are active, or are what you think they are.
  Every group, venue and event you name must come from a tool result.
- NEVER invent, embellish or guess at buddy matches. Present only what
  find_activity_buddies returned, exactly as given.
- If the tools return little, SAY SO plainly and offer the Activity Buddy
  board: they can post what they're looking for and be found by others.
  A short honest answer beats a padded one.
- If a tool returns an 'error' key, say that source is unavailable and
  continue with the others.

Links: markdown on the name — [The Court Club](maps_url) — never a bare URL.

Buddy results: if matches is empty, say no buddies match yet and suggest
creating a post from the Activity Buddies panel. End buddy suggestions by
noting they can send a request to join from the panel, and remind them to
meet in public places.

Format: short intro line, then suggestions. Under 180 words. End by asking
which one they'd like woven into their weekly plan."""


def build_agent():
    cfg = settings()
    llm = ChatVertexAI(model=cfg.vital_model, temperature=0.4,
                       project=cfg.google_cloud_project, location=cfg.google_cloud_location)
    return create_react_agent(
        llm, tools=[get_user_interests, find_activity_buddies,
                    search_places, search_events],
        prompt=SYSTEM_PROMPT)
