"""The status vocabulary.

The status line used to read `activity_scout: using search_places`. Two
problems: it reads like machinery, and it hands anyone watching the network
tab a map of the internal topology.

These tests protect both properties — variety, and not leaking internals —
because both degrade silently. Nobody notices a status line getting worse.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import vocabulary

# Every tool actually wired to an agent. Kept explicit so adding a tool
# without a phrase fails here rather than shipping "Working on it".
WIRED_TOOLS = [
    "get_weather", "search_places", "search_events",
    "find_activity_buddies", "get_user_interests",
    "log_sleep", "get_sleep_history", "analyze_sleep_data",
    "forecast_energy",
]


def test_there_are_around_fifty_phrases():
    """Enough that a session does not repeat itself into wallpaper."""
    assert vocabulary.word_count() >= 45


def test_every_wired_tool_has_its_own_phrases():
    for tool in WIRED_TOOLS:
        assert tool in vocabulary.PHRASES, (
            f"{tool} has no status phrases, so it would show the generic "
            "fallback while everything else reads specifically")


def test_no_phrase_leaks_an_internal_name():
    """A status line naming a tool, node or provider tells a stranger how
    the system is built. It is also just worse copy.

    Word boundaries, not substrings: "api" is inside "shaping", which had
    this test failing on a phrase that was perfectly fine. A guard that
    cries wolf gets deleted, and then it guards nothing.
    """
    import re

    internals = ["search_places", "get_weather", "activity_scout",
                 "sleep_energy", "people_connector", "idea_generator",
                 "langgraph", "vertex", "gemini", "fitbit", "e2b",
                 "tool", "tools", "api", "endpoint", "agent", "node",
                 "query", "function", "llm", "model", "prompt"]
    pattern = re.compile(r"\b(" + "|".join(internals) + r")\b")
    for phrases in list(vocabulary.PHRASES.values()) + list(
            vocabulary.AGENT_OPENERS.values()):
        for phrase in phrases:
            found = pattern.search(phrase.lower())
            assert not found, f"{phrase!r} leaks {found.group(0)!r}"


def test_phrases_read_as_plain_language():
    for phrases in vocabulary.PHRASES.values():
        for phrase in phrases:
            assert phrase[0].isupper(), f"{phrase!r} should start capitalised"
            assert not phrase.endswith("."), f"{phrase!r} is a status, not a sentence"
            assert 3 <= len(phrase.split()) <= 9, f"{phrase!r} is the wrong length"


def test_the_same_call_always_reads_the_same():
    """A status line that re-rolls on every React render flickers."""
    first = vocabulary.for_tool("search_places", seed="run-abc")
    for _ in range(20):
        assert vocabulary.for_tool("search_places", seed="run-abc") == first


def test_different_calls_read_differently():
    """Otherwise it is one phrase per tool forever, which is wallpaper
    again — just politer wallpaper."""
    seen = {vocabulary.for_tool("search_places", seed=f"run-{i}")
            for i in range(40)}
    assert len(seen) > 1


def test_an_unknown_tool_still_says_something_sensible():
    """A tool added without a phrase must not render an empty status or
    crash the stream."""
    line = vocabulary.for_tool("some_future_tool", seed="x")
    assert line and line in vocabulary.DEFAULT


def test_agent_openers_exist_for_every_specialist():
    for node in ["sleep_energy", "activity_scout", "idea_generator",
                 "people_connector", "planner"]:
        assert vocabulary.for_agent(node), f"{node} has no opener"


def test_non_agent_nodes_stay_silent():
    """The supervisor and the approval gate are plumbing. Announcing them
    would narrate the architecture at the user."""
    for node in ["supervisor", "request_approval", "commit_plan", ""]:
        assert vocabulary.for_agent(node) == ""


def test_the_stream_emits_a_phrase_not_a_function_name(monkeypatch):
    """End to end through the real SSE path: whatever reaches the browser
    must not contain the tool's name."""
    pytest.importorskip("langchain_google_vertexai")
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import vital.api as api

    class ToolGraph:
        async def astream_events(self, _inputs, config=None, version=None):
            yield {"event": "on_tool_start", "run_id": "r1",
                   "name": "search_places",
                   "metadata": {"langgraph_node": "activity_scout"}, "data": {}}
            yield {"event": "on_tool_end", "run_id": "r1",
                   "name": "search_places",
                   "metadata": {"langgraph_node": "activity_scout"},
                   "data": {"output": {"venues": []}}}

        def get_state(self, _config):
            return SimpleNamespace(tasks=(), values={"messages": [],
                                                     "routing_history": []})

    monkeypatch.setattr(api, "graph", ToolGraph())
    client = TestClient(api.app)
    body = client.post("/chat", json={"message": "find me a climbing gym"}).text

    assert "search_places" not in body, "the tool name reached the browser"
    assert "using" not in body.lower() or "status" in body
    assert any(p in body for p in vocabulary.PHRASES["search_places"]), (
        "no human-readable status reached the browser")
