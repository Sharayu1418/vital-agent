"""Activity Scout — Phase 0's single agent.

In Phase 1 this becomes a subgraph under the supervisor. Design rule (D1):
it must only ever communicate through its input/output messages — no shared
imports with other agents.
"""
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

from vital.config import settings
from vital.tools.places import search_places
from vital.tools.weather import get_weather

SYSTEM_PROMPT = """You are Activity Scout, part of VITAL — an assistant that \
replaces 'google it yourself' with direct, personalized recommendations.

Given the user's energy level, mood, location, budget and free time:
1. If any of location or available time is missing, ask ONCE, briefly.
2. ALWAYS call get_weather before considering outdoor activities.
3. Call search_places with specific queries to find real venues.
4. Recommend exactly 3 activities: mix indoor/outdoor according to weather \
and the user's energy level. High energy → physical options first.

Format each recommendation as:
**<activity>** at <venue> (<rating>★) — <one line on why it fits them>
<maps_url>

Rules:
- Real venues with links, never generic advice like 'try a new hobby'.
- Respect budget signals ('broke', 'cheap' → free/low-cost options).
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
        tools=[get_weather, search_places],
        prompt=SYSTEM_PROMPT,
    )
