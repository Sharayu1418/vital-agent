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


# ---------- knowing the people you already matched with ----------

def test_it_can_look_up_the_users_own_buddy_requests(monkeypatch):
    """The gap this closes, taken from a real transcript.

    User: "isn't sharayu the person who accepted my request for swimming?"
    VITAL: "I don't have access to information about past requests you've
            made or who has accepted them."

    That answer was honest — the agent had find_activity_buddies, which
    searches STRANGERS' open posts, and nothing else. Sharayu was not on the
    board advertising for a swimming partner; she had already accepted. So
    the search correctly returned nothing, and the agent correctly reported
    it had no way to look.

    Meanwhile buddies.my_requests() had the answer, and the API was already
    serving it to the panel at GET /activity-requests/mine. The data was one
    table away from a user being told it did not exist.
    """
    assert "my_activity_buddies" in _wired_tools(monkeypatch)


def test_the_lookup_never_leaks_a_raw_user_id(monkeypatch):
    """Buddy matching is between strangers. Display names and an approximate
    city are the contract; a raw user_id is a stable identifier that would
    let one user correlate someone across every post they ever made."""
    monkeypatch.setattr(people_connector.buddies, "my_requests", lambda _u: {
        "incoming": [{"id": 1, "post_id": 7, "requester_name": "Sharayu",
                      "message": "keen!", "status": "accepted",
                      "created_at": "2026-08-12", "activity": "swimming",
                      "post_display_name": "S."}],
        "outgoing": [{"id": 2, "post_id": 9, "message": "", "status": "pending",
                      "created_at": "2026-08-14", "activity": "pickleball",
                      "display_name": "Ravi", "city": "Albany"}],
    })
    # set(), not monkeypatching get() — a ContextVar's methods are read-only.
    people_connector.storage.current_user_id.set("anon-secret-session-id")

    out = people_connector.my_activity_buddies.invoke({})
    assert "anon-secret-session-id" not in repr(out)
    for key in ("user_id", "requester_user_id"):
        assert key not in repr(out), f"{key} must never reach the model"


def test_the_lookup_degrades_instead_of_killing_the_turn(monkeypatch):
    """Same contract as every other tool here: a storage failure becomes an
    'error' key the prompt knows how to report, not an exception that loses
    the user's message."""
    def boom(_user_id):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(people_connector.buddies, "my_requests", boom)
    people_connector.storage.current_user_id.set("u1")
    out = people_connector.my_activity_buddies.invoke({})
    assert "error" in out and "database is on fire" not in out["error"]


def test_the_agent_cannot_accept_or_decline_on_the_users_behalf(monkeypatch):
    """Read-only by construction, not by instruction.

    Accepting a request shares the user's approximate location with another
    real person. Same reasoning as commit_plan: the safe thing is that the
    model has no path to the code, rather than a prompt asking it not to.
    """
    wired = _wired_tools(monkeypatch)
    for forbidden in ("decide_request", "accept_request", "create_request",
                      "block_user", "report_post"):
        assert forbidden not in wired, (
            f"{forbidden} is wired into the agent — deciding on a buddy "
            "request must stay a click the user makes")


def test_the_prompt_separates_a_lookup_from_a_search():
    """The failure was a routing mistake, so the fix has to be about
    routing. Without this the model reaches for the search it already knows
    and reports 'no matches' for somebody the user is already talking to."""
    lowered = " ".join(people_connector.SYSTEM_PROMPT.lower().split())
    assert "my_activity_buddies" in lowered
    assert "lookup" in lowered
    assert "never say you have no access" in lowered
