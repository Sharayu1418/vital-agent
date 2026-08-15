"""Activity Scout — Phase 0's single agent.

In Phase 1 this becomes a subgraph under the supervisor. Design rule (D1):
it must only ever communicate through its input/output messages — no shared
imports with other agents.
"""
from langchain_core.tools import tool
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

from vital.config import settings
from vital.tools.places import search_places
from vital.tools.weather import get_weather


@tool
def find_activities(query: str, limit: int = 3) -> dict:
    """Find and RANK real places for an activity, personalised to this user.

    Prefer this over search_places for any 'what should I do' question. It
    searches ~40 candidates and ranks them by how well each fits the user's
    predicted energy at the time, what VITAL knows they like and dislike,
    distance, review-weighted quality, and whether it was suggested
    recently.

    Give a specific activity query: 'bouldering gym', 'pottery class',
    'quiet cafe' — not 'things to do'.

    Returns venues ALREADY IN ORDER, each with `why` (the reasons it
    ranked) and `signals` (the scores behind it). Present them in the order
    given and use `why` to explain each one. Do not reorder, substitute or
    add venues — the ranking accounts for things you cannot see."""
    from vital import forecast as engine
    from vital import recommend, storage

    user_id = storage.current_user_id.get()
    here = storage.current_location.get()

    # Rank against energy at the hour they would actually go, not now.
    nights = engine.nights_from_rows(
        storage.sleep_history(engine.DEBT_WINDOW_NIGHTS * 2),
        storage.health_rows(user_id))
    forecast = engine.forecast(nights, storage.local_now(), horizon_hours=12)
    peak = forecast.peak()
    predicted = peak.energy if peak else 0.6

    return recommend.find_activities(
        query, user_id=user_id, predicted_energy=predicted,
        lat=here["lat"] if here else None,
        lng=here["lng"] if here else None,
        city=(here or {}).get("label", ""), limit=limit)


SYSTEM_PROMPT = """You are Activity Scout, part of VITAL — an assistant that \
replaces 'google it yourself' with direct, personalized recommendations.

Given the user's energy level, mood, location, budget and free time:
0. If a tool you called returned an 'error' key, SAY SO FIRST, before
   anything else. Asking the user a follow-up question instead makes an
   outage look like a normal conversation, and they will never find out
   the search failed. Reporting it is not optional and is not replaced by
   asking for more detail.
1. If any of location or available time is missing, ask ONCE, briefly.
2. ALWAYS call get_weather before considering outdoor activities.
3. Call find_activities with a specific activity query. It returns venues
   ALREADY RANKED for this user — by predicted energy at the time, what
   VITAL knows they like, distance, and review-weighted quality.
4. Present them IN THE ORDER GIVEN and explain each using its `why`. Do
   NOT reorder, substitute or invent venues. The ranking sees things you
   cannot — the user's energy forecast, their stored preferences, what was
   suggested last week — so overriding it makes the answer worse while
   looking more decisive.
   Use search_places only when find_activities is unavailable.

Format each recommendation as ONE line, with the venue as a markdown link:
**<activity>** at [<venue>](<maps_url>) (<rating>★) — <why it fits them>

Rules:
- Real venues with links, never generic advice like 'try a new hobby'.
- Say WHY each one, using its `why` list — "10 minutes away and it suits
  the energy you'll have at 11" is the answer; "this is a great gym" is
  not. The reasons are computed; passing them on is most of the value.
- NEVER paste a bare maps_url. They are long ?cid= links that wrap across \
three lines and make the whole answer unreadable. Always [venue](url).
- Respect budget signals ('broke', 'cheap' → free/low-cost options).
- If a tool returns an 'error' key: tell the user live data is unavailable, \
then give best-effort suggestions clearly marked as unverified. Never pretend.
- Keep the whole reply under 200 words. No filler.
"""


def build_agent():
    cfg = settings()
    llm = ChatVertexAI(
        model=cfg.vital_model,   # D5: Flash — this is tool-use + formatting, not deep reasoning
        temperature=0.3,
        project=cfg.google_cloud_project,
        location=cfg.google_cloud_location,
    )
    return create_react_agent(
        llm,
        # find_activities first: it is the one that ranks. search_places
        # stays as the fallback the prompt names, not as an equal option.
        tools=[get_weather, find_activities, search_places],
        prompt=SYSTEM_PROMPT,
    )
