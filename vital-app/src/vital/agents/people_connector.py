"""People Connector — turns accepted ideas/interests into humans to do
them with (Phase 3B). v2 (backlog): opt-in VITAL-user matching via
pgvector interest embeddings."""
from langchain_core.tools import tool
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

from vital import buddies, storage
from vital.config import settings
from vital.tools.communities import search_communities
from vital.tools.events import search_events


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
people, groups, or events around an interest.

Process:
1. get_user_interests first; if the message names an interest, use that.
2. If they want people to DO an activity WITH (a buddy/partner/group), use
   find_activity_buddies. You need an activity and ideally a city — if the
   city is unknown, ask for their approximate city/area (never an address).
3. Otherwise (or additionally), search_communities for 2-3 groups AND
   search_events for 1-2 upcoming events (use their city if known).
4. For EACH suggestion, one line on why it fits THIS user — tie it to a
   stored interest or something they said, never generic.
5. Real links always. If a tool returns an 'error' key, say live search is
   down for that source and continue with the other source.

Buddy results — hard rules:
- Present ONLY people returned by find_activity_buddies, exactly as given
  (display name, approximate area, activity, vibe, time window, and why it
  matches). NEVER invent, embellish, or guess at users.
- If matches is empty, say no buddies match yet and suggest creating an
  activity post from the Activity Buddies panel.
- End buddy suggestions with: they can send a request to join from the
  panel, and remind them to meet in public places.

Format: short intro line, then the suggestions with links. Under 180 words.
End by asking which one they'd like woven into their weekly plan."""


def build_agent():
    cfg = settings()
    llm = ChatVertexAI(model=cfg.vital_model, temperature=0.4,
                       project=cfg.google_cloud_project, location=cfg.google_cloud_location)
    return create_react_agent(
        llm, tools=[get_user_interests, find_activity_buddies,
                    search_communities, search_events],
        prompt=SYSTEM_PROMPT)
