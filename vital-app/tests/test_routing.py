"""Routing eval — the regression contract (D12). Seed for Phase 4's 50-case suite.

These hit real Vertex AI (cheap: Flash, ~30 tokens/case). Run explicitly:

    VITAL_LIVE_EVALS=1 uv run pytest tests/test_routing.py -v

Gate: >= 90% (Phase 1 DoD was 18/20; now 26 cases with Phase 3 routes).
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
    ("are there pottery groups near me?", "people_connector"),
    ("I want to meet people who are into climbing", "people_connector"),
    ("find me a running club in Brooklyn", "people_connector"),
    ("ok plan my weekend around all this", "planner"),
    ("put the run and the meetup on my calendar", "planner"),
    ("make me a schedule for next week", "planner"),
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


# ---------- topology: one supervisor call, one specialist ----------

def test_agent_nodes_end_the_turn_instead_of_looping_back():
    """Production ran 5 agent hops per message. Agents returned to the
    supervisor via the memory writer, and the supervisor could not tell an
    agent had already answered, so it re-routed until the guard fired.
    Agents now go straight to END."""
    from langgraph.graph import END
    from vital.graph import _agent_node

    class FakeAgent:
        def invoke(self, payload):
            from langchain_core.messages import AIMessage
            return {"messages": [*payload["messages"], AIMessage(content="answered")]}

    class FakeStore:
        def search(self, *_a, **_k):
            return []

    node = _agent_node(FakeAgent(), FakeStore())
    cmd = node({"messages": [("user", "hi")], "user_id": "u1"})
    assert cmd.goto == END, "an agent must not hand control back to the supervisor"


def test_memory_extraction_is_no_longer_a_graph_node():
    """It sat between the answer and `done`, locking the composer during a
    model call the user cannot see. It is called from the API after `done`."""
    import vital.graph as graph_mod
    assert not hasattr(graph_mod, "_memory_writer")
    assert callable(graph_mod.write_memories)


def test_write_memories_never_raises():
    """Memory must never break a conversation."""
    from vital.graph import write_memories

    class Boom:
        def with_structured_output(self, _s):
            raise RuntimeError("model down")

    assert write_memories("u1", [], llm=Boom()) == 0          # empty tail
    from langchain_core.messages import AIMessage
    assert write_memories("u1", [AIMessage(content="hi")], store=object(),
                          llm=Boom()) == 0                     # extraction fails


# ---------- P1-6: conversation history is bounded ----------

def test_short_conversations_are_untouched():
    from vital.graph import trim_history
    msgs = [("user", "hi"), ("ai", "hello")]
    assert trim_history(msgs) == msgs


def test_long_conversations_are_trimmed_to_the_recent_slice():
    """Every turn used to resend the whole thread — cost and latency grew
    linearly, and a long enough conversation would exceed the context window
    and fail mid-chat."""
    from vital.graph import trim_history
    msgs = [("user", f"m{i}") for i in range(100)]
    kept = trim_history(msgs, limit=20)
    assert len(kept) == 20
    assert kept[-1] == ("user", "m99"), "must keep the MOST RECENT turns"
    assert kept[0] == ("user", "m80")


def test_trim_keeps_the_latest_user_message():
    """The agent reads the last human message to build its memory query; if
    trimming dropped it, recall would silently query the wrong thing."""
    from vital.graph import trim_history
    msgs = [("ai", f"a{i}") for i in range(50)] + [("user", "what should I do today?")]
    kept = trim_history(msgs, limit=5)
    assert kept[-1] == ("user", "what should I do today?")


def test_the_limit_is_configurable():
    from vital.config import settings
    from vital.graph import trim_history
    msgs = [("user", f"m{i}") for i in range(100)]
    assert len(trim_history(msgs)) == settings().history_limit
