"""Grounding: what the app says must come from what it looked up.

THE RISK THIS COVERS
--------------------
A language model asked for climbing gyms in Albany will happily produce
five plausible, well-formatted, entirely fictional gyms. It reads exactly
like a good answer. Nobody notices until they drive to one.

The People Connector prompt already forbids naming unverifiable
communities — that rule exists precisely because the model was filling the
gap from memory. But a prompt is a request, not a constraint, and nothing
in the codebase checked that the request was honoured.

WHAT IS TESTED
--------------
Two levels, because they fail differently:

1. **Offline, deterministic** — the extractor that pulls venue-like claims
   out of an answer, and the comparison against what the tools returned.
   This is the machinery, and it runs on every CI push.
2. **Live** (GROUNDING_LIVE_EVAL=1) — a real model, a real tool result,
   and a check that the answer names nothing the tool did not return. That
   one costs money and needs credentials, so it stays explicit.

The offline half cannot prove the model behaves. It proves that IF it
misbehaves, we would see it — which is the part that was missing.
"""
import os
import re

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

# Markdown links are how venues are supposed to be rendered — the places
# tool's docstring requires it, so the model can read the venue name back
# out of its own answer. Anything else claiming to be a place is exactly
# what we are looking for.
LINK = re.compile(r"\[([^\]]{2,60})\]\(([^)]+)\)")


def named_places(answer: str) -> set[str]:
    """Venue-ish names an answer commits to.

    Deliberately over-inclusive on links and cautious elsewhere: a false
    positive here costs a test failure somebody has to read, while a false
    negative means a fabricated venue ships.
    """
    return {match.group(1).strip().lower() for match in LINK.finditer(answer)}


def ungrounded(answer: str, tool_output: dict) -> set[str]:
    """Names in the answer that no tool returned."""
    supplied = {str(v.get("name", "")).strip().lower()
                for v in (tool_output or {}).get("venues", [])}
    supplied |= {str(e.get("name", "")).strip().lower()
                 for e in (tool_output or {}).get("events", [])}
    supplied.discard("")
    return {name for name in named_places(answer) if name not in supplied}


# ---------- the detector itself ----------

def test_it_finds_a_venue_the_tool_never_returned():
    """The failure case, stated plainly. If this test cannot catch an
    invented gym, nothing downstream can."""
    answer = ("Try [The Court Club](https://maps.google.com/?cid=1) or "
              "[Albany Boulder Collective](https://maps.google.com/?cid=2).")
    tools = {"venues": [{"name": "The Court Club"}]}
    assert ungrounded(answer, tools) == {"albany boulder collective"}


def test_a_fully_grounded_answer_is_clean():
    answer = "[The Court Club](https://maps.google.com/?cid=1) looks right."
    assert ungrounded(answer, {"venues": [{"name": "The Court Club"}]}) == set()


def test_case_and_whitespace_do_not_create_false_positives():
    """The model reformats names constantly. A grounding check that fires
    on capitalisation gets muted within a week."""
    answer = "[  the COURT club ](https://maps.google.com/?cid=1)"
    assert ungrounded(answer, {"venues": [{"name": "The Court Club"}]}) == set()


def test_prose_without_links_is_not_flagged():
    """Only committed claims count. 'somewhere with a bouldering wall' is
    advice, not a fabricated venue."""
    answer = "Look for somewhere with a bouldering wall and a quiet cafe."
    assert ungrounded(answer, {"venues": []}) == set()


def test_an_answer_with_no_tool_output_at_all_flags_every_named_place():
    """When the tool failed, the honest answer names nothing. This is the
    exact shape of the Reddit incident: the tool returned an error and the
    model filled the silence."""
    answer = "Try [Made-Up Gym](https://example.com)."
    assert ungrounded(answer, {"error": "venue search unavailable"}) == {"made-up gym"}


# ---------- the contract that makes this checkable ----------

def test_the_places_tool_still_requires_markdown_links():
    """This whole check depends on venues being rendered as markdown links
    so their names can be read back out. If that instruction is dropped
    from the tool description, grounding becomes unverifiable and this test
    is the only thing that would say so."""
    from vital.tools.places import search_places

    description = search_places.description.lower()
    assert "markdown link" in description


def test_the_people_connector_still_forbids_inventing_communities():
    """The prompt rule that this test exists to enforce. If it is ever
    removed, the model will fill the gap from memory again — that is
    observed behaviour, not a hypothetical."""
    from vital.agents.people_connector import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "never" in lowered or "do not" in lowered
    assert any(word in lowered for word in ("invent", "make up", "from memory"))


# ---------- live: does the model actually behave? ----------

pytestmark_live = pytest.mark.skipif(
    os.environ.get("GROUNDING_LIVE_EVAL") != "1",
    reason="needs Vertex credentials; set GROUNDING_LIVE_EVAL=1")


@pytestmark_live
def test_a_real_answer_names_only_what_the_tool_returned():
    """The behavioural check. Offline tests prove we could SEE a
    fabrication; this one asks whether there is one."""
    pytest.importorskip("langchain_google_vertexai")
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_google_vertexai import ChatVertexAI

    from vital.agents.activity_scout import SYSTEM_PROMPT
    from vital.config import settings

    cfg = settings()
    llm = ChatVertexAI(model=cfg.vital_model, temperature=0.0,
                       project=cfg.google_cloud_project,
                       location=cfg.google_cloud_location)

    tools = {"venues": [
        {"name": "The Court Club", "maps_url": "https://maps.google.com/?cid=1",
         "rating": 4.5, "address": "1 Main St, Albany"},
    ]}
    answer = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content="climbing gyms in Albany?"),
        SystemMessage(content=f"search_places returned: {tools}. Answer using "
                              "ONLY these results, as markdown links."),
    ]).content

    invented = ungrounded(str(answer), tools)
    assert not invented, f"the model named venues no tool returned: {invented}"
