"""People Connector — turns accepted ideas/interests into humans to do
them with (Phase 3B).

No third-party community provider, deliberately. Reddit, Meetup, Facebook
Groups, Eventbrite search and Strava clubs all closed or went paid between
2019 and 2026; the Reddit integration sat dead in production for months
while degrading gracefully enough that nobody noticed. Community discovery
now runs on things we own or that are commoditised: the Activity Buddy
Board (ours), Google Places (where an activity actually happens), and
Ticketmaster (ticketed events). See providers/__init__.py for why.

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


@tool
def my_activity_buddies() -> dict:
    """The user's OWN buddy requests — who they asked, who asked them, and
    which were accepted. Read this before answering anything about a named
    person, a past request, or an existing connection.

    find_activity_buddies searches STRANGERS' open posts. It cannot answer
    "who accepted my swimming request" or "am I still waiting on anyone",
    because those are facts about this user, not an advert on the board.

    Returns three lists.

    'connections' — people they have ACTUALLY agreed to do something with.
    This is the durable one: it survives the post being closed or deleted,
    so it is where to look for "the person I swim with" months later. Each
    entry has a name, the activity, and when it started.

    'outgoing' / 'incoming' — individual requests and their state, carrying
    the activity, a display name, a status of pending/accepted/rejected, and
    the date. Use these for "am I still waiting to hear back".

    Accepting or declining is NOT available to you — that is a click the
    user makes in the Activity Buddies panel, because it shares their
    approximate location with another person.
    """
    try:
        user_id = storage.current_user_id.get()
        return {"connections": buddies.my_connections(user_id),
                **buddies.my_requests(user_id),
                "safety_note": buddies.SAFETY_NOTE}
    except Exception as exc:  # storage failure must degrade, not crash the turn
        return {"error": f"buddy history unavailable ({type(exc).__name__})"}


SYSTEM_PROMPT = """You are VITAL's People Connector. The user wants to find \
people, groups, or places to share an interest with.

FIRST, decide which question you are being asked.

If it is about someone the user ALREADY has a connection with — a name, "who
accepted", "did anyone reply", "the person I matched with", "am I still
waiting", "who do I swim with", "can I go again with them" — call
my_activity_buddies. That is a LOOKUP, not a search. Running
find_activity_buddies instead searches strangers' adverts and comes back
empty, which reads as "that person does not exist" when they are sitting in
the user's accepted list.

Never say you have no access to their requests or history. You do. Call
my_activity_buddies and answer from it. If it returns nothing relevant, say
that specifically — "I don't see an accepted swimming request" is useful;
"I can't see your requests" is false.

Otherwise the user wants NEW people, and the order below applies.

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
        # my_activity_buddies before find_activity_buddies: the lookup is the
        # one people reach for by name, and the search is the fallback.
        llm, tools=[get_user_interests, my_activity_buddies,
                    find_activity_buddies, search_places, search_events],
        prompt=SYSTEM_PROMPT)
