"""Routing eval — the regression contract (D12). Seed for Phase 4's 50-case suite.

These hit real Vertex AI (cheap: Flash, ~30 tokens/case). Run explicitly:

    VITAL_LIVE_EVALS=1 uv run pytest tests/test_routing.py -v

Gate (Phase 1 DoD): >= 18/20.
"""
import os

import pytest

LIVE = os.environ.get("VITAL_LIVE_EVALS") == "1"

CASES = [
    ("I'm exhausted lately", "sleep_energy"),
    ("slept terribly, feel like a zombie", "sleep_energy"),
    ("log my sleep: bed at 1am, up at 7, quality 2", "sleep_energy"),
    ("why am I always tired at 3pm", "sleep_energy"),
    ("slept 4 hours but still want to go out tonight", "sleep_energy"),
    ("bored, what should I do this weekend", "activity_scout"),
    ("find me something fun near Prospect Park", "activity_scout"),
    ("it's raining, what can I do indoors today", "activity_scout"),
    ("I have 3 free hours and $20", "activity_scout"),
    ("I'm tired of being bored", "activity_scout"),
    ("any good climbing gyms in Brooklyn?", "activity_scout"),
    ("I have all this energy and nothing to point it at", "idea_generator"),
    ("I want a new hobby but don't know what", "idea_generator"),
    ("feeling restless and directionless", "idea_generator"),
    ("give me a side project idea", "idea_generator"),
    ("what should I build this summer", "idea_generator"),
    ("thanks, that's all!", "FINISH"),
    ("perfect, that works", "FINISH"),
    ("ok great, bye", "FINISH"),
    ("cool, I'll try the pottery class", "FINISH"),
]


@pytest.mark.skipif(not LIVE, reason="live eval: set VITAL_LIVE_EVALS=1")
@pytest.mark.parametrize("message,expected", CASES)
def test_route(message: str, expected: str):
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_google_vertexai import ChatVertexAI

    from vital.config import settings
    from vital.supervisor import ROUTER_PROMPT, Route

    cfg = settings()
    llm = ChatVertexAI(model=cfg.vital_model, temperature=0.0,
                       project=cfg.google_cloud_project, location=cfg.google_cloud_location)
    decision = llm.with_structured_output(Route).invoke(
        [SystemMessage(content=ROUTER_PROMPT), HumanMessage(content=message)]
    )
    assert decision.next == expected, f"'{message}' -> {decision.next} ({decision.reasoning})"
