"""People Connector wiring: which tools it has, and which it must not.

The point of these tests is to keep a decision from silently eroding.
Community discovery deliberately has NO third-party provider — every one
closed between 2019 and 2026, and the Reddit integration sat dead in
production for months because graceful degradation reported it as a
transient outage every time. See docs/LIMITATIONS.md.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import importlib

import pytest

from vital.agents import people_connector


def _wired_tools(monkeypatch):
    """The tools ACTUALLY handed to create_react_agent.

    Reading the module's imported symbols instead would pass even if a tool
    were removed from the agent's list — verified by mutation, which is how
    this helper came to exist.
    """
    captured = {}

    def fake_create(llm, tools=None, prompt=None):
        captured["names"] = [t.name for t in tools]
        return object()

    monkeypatch.setattr(people_connector, "create_react_agent", fake_create)
    monkeypatch.setattr(people_connector, "ChatVertexAI", lambda **kw: object())
    people_connector.build_agent()
    return captured["names"]


def test_the_reddit_module_is_gone():
    """Not just unwired — removed. A dormant module invites re-wiring
    without revisiting why it was dropped."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("vital.tools.communities")


def test_no_community_search_tool_is_wired_in(monkeypatch):
    assert not hasattr(people_connector, "search_communities")
    assert not any("communit" in n for n in _wired_tools(monkeypatch))


def test_it_can_find_places_where_an_activity_happens(monkeypatch):
    """Places replaces the dead provider: where the activity happens is
    where its community is, and Places has genuinely global coverage."""
    assert "search_places" in _wired_tools(monkeypatch)


def test_it_keeps_the_owned_and_commoditised_sources(monkeypatch):
    """Buddies (ours), places and events (commoditised infrastructure) —
    the two dependency classes that don't get gated."""
    names = _wired_tools(monkeypatch)
    assert "find_activity_buddies" in names
    assert "search_events" in names
    assert "get_user_interests" in names


def test_the_prompt_forbids_inventing_online_communities():
    """Without a tool to verify them, the model will otherwise fill the gap
    with subreddits and Discord servers from memory. Omission isn't enough;
    the prohibition has to be explicit."""
    prompt = people_connector.SYSTEM_PROMPT.lower()
    assert "never suggest online communities" in prompt
    for forbidden in ("subreddit", "discord", "forum"):
        assert forbidden in prompt, f"prompt should name {forbidden} explicitly"
    assert "must come from a tool result" in prompt


def test_the_prompt_puts_real_people_before_venues():
    """Ordering is the product decision: the Activity Buddy board is the
    only source of actual people, so it is tried first."""
    prompt = people_connector.SYSTEM_PROMPT
    assert prompt.index("find_activity_buddies") < prompt.index("search_places")
    assert prompt.index("search_places") < prompt.index("search_events")


def test_the_prompt_asks_for_gathering_places_not_generic_venues():
    prompt = people_connector.SYSTEM_PROMPT.lower()
    assert "gathering place" in prompt
    assert any(example in prompt for example in ("run club", "bouldering gym"))


def test_the_prompt_requires_markdown_links():
    """Bare maps_url values wrap across three lines and swamp the answer."""
    assert "markdown" in people_connector.SYSTEM_PROMPT.lower()


def test_the_prompt_prefers_an_honest_short_answer_to_a_padded_one():
    prompt = people_connector.SYSTEM_PROMPT.lower()
    assert "say so" in prompt
    assert "beats a padded one" in prompt
