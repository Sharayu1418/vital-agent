"""Idea Generator agent — turns directionless energy into concrete projects."""
from langchain_core.tools import tool
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

from vital import storage
from vital.config import settings


@tool
def get_user_interests() -> list[str]:
    """The user's known interests. Check BEFORE generating ideas."""
    return storage.interests()


@tool
def add_interest(interest: str) -> str:
    """Save an interest the user reveals (e.g. 'ceramics', 'bouldering').
    One or two words, lowercase."""
    storage.add_interest(interest)
    return "saved"


@tool
def get_saved_ideas() -> list[dict]:
    """Ideas already suggested and accepted. NEVER re-suggest these."""
    return storage.saved_ideas()


@tool
def save_idea(idea: str, category: str) -> str:
    """Save an idea the user liked. category: creative|physical|social|learning|building."""
    storage.save_idea(idea, category)
    return "saved"


SYSTEM_PROMPT = """You are VITAL's Idea Generator. The user has energy but no \
direction — your job is 3 concrete, startable-this-week ideas.

Process:
1. get_user_interests and get_saved_ideas first. If interests are empty,
   ask 2 quick questions, save answers with add_interest.
2. Generate exactly 3 ideas scored against: matches interests, fits their
   stated free time, novel (not in saved ideas).
3. Each idea: **name** — first concrete step + rough cost. No 'consider
   exploring...' language; every idea starts with a verb.
4. If they like one, save_idea it.

Under 180 words."""


def build_agent():
    cfg = settings()
    llm = ChatVertexAI(model=cfg.vital_model, temperature=0.7,  # creativity task
                       project=cfg.google_cloud_project, location=cfg.google_cloud_location)
    return create_react_agent(
        llm, tools=[get_user_interests, add_interest, get_saved_ideas, save_idea],
        prompt=SYSTEM_PROMPT)
