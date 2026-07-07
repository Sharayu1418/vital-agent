"""People Connector — turns accepted ideas/interests into humans to do
them with (Phase 3B). v2 (backlog): opt-in VITAL-user matching via
pgvector interest embeddings."""
from langchain_core.tools import tool
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

from vital import storage
from vital.config import settings
from vital.tools.communities import search_communities
from vital.tools.events import search_events


@tool
def get_user_interests() -> list[str]:
    """The user's saved interests — check BEFORE searching so suggestions
    connect to what they already care about."""
    return storage.interests()


SYSTEM_PROMPT = """You are VITAL's People Connector. The user wants to find \
people, groups, or events around an interest.

Process:
1. get_user_interests first; if the message names an interest, use that.
2. search_communities for 2-3 groups AND search_events for 1-2 upcoming
   events (use their city if known from context).
3. For EACH suggestion, one line on why it fits THIS user — tie it to a
   stored interest or something they said, never generic.
4. Real links always. If a tool returns an 'error' key, say live search is
   down for that source and continue with the other source.

Format: short intro line, then the suggestions with links. Under 180 words.
End by asking which one they'd like woven into their weekly plan."""


def build_agent():
    cfg = settings()
    llm = ChatVertexAI(model=cfg.vital_model, temperature=0.4,
                       project=cfg.google_cloud_project, location=cfg.google_cloud_location)
    return create_react_agent(
        llm, tools=[get_user_interests, search_communities, search_events],
        prompt=SYSTEM_PROMPT)
