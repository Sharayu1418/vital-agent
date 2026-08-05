"""Supervisor: routes each turn to a sub-agent via structured output.

Routing is classification → Flash model (D5). Quality lives in the few-shots
below; when routing evals fail, fix THIS prompt, don't upgrade the model.
"""
from typing import Literal

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END
from langgraph.types import Command
from pydantic import BaseModel, Field

# Loop guard. Was 5, and production hit it on EVERY turn: the router was
# handed the raw message list with no indication that an agent had already
# answered, so it re-read the same latest user message and routed again until
# the guard cut it off. Turns cost ~5x in both latency (37-57s) and tokens
# (12-16k). The real fix is the routing-history block below; this is now just
# a backstop, and a turn that genuinely needs a third specialist does not
# exist in practice.
MAX_HOPS = 2

ROUTER_PROMPT = """You route user messages to the right VITAL agent.

Agents:
- activity_scout: finding things to DO — activities, venues, weekend options
- sleep_energy: sleep quality, tiredness, energy management, logging sleep
- idea_generator: directionless energy, wanting projects/hobbies/purpose
- people_connector: finding PEOPLE — groups, communities, meetups, events to
  attend with others, 'who else does X'
- planner: assembling a concrete SCHEDULE from what's been discussed —
  'plan my week/weekend', 'put this on my calendar', 'make me a plan'
- FINISH: user is done, says thanks, or the last agent reply fully answered them

Examples:
"I'm exhausted lately" -> sleep_energy
"bored, what should I do this weekend" -> activity_scout
"I have energy but no direction" -> idea_generator
"I'm tired of being bored" -> activity_scout  (boredom is the complaint, not sleep)
"slept 4 hours, still want to go out tonight" -> sleep_energy  (health first)
"are there pottery groups near me?" -> people_connector
"I want to meet people who are into climbing" -> people_connector
"ok, plan my weekend around all this" -> planner
"put the run and the meetup on my calendar" -> planner
"thanks, that's all" -> FINISH

Route based on the LATEST user message in context of the conversation.
If a sub-agent just fully answered and no new user input is needed, FINISH."""

# The router used to see only the message list, which does not say who has
# already spoken THIS turn — an agent's reply looks like any other assistant
# message. So it re-read the same user message and routed to the same agent
# again, every time, until MAX_HOPS stopped it. Stating it plainly fixes it.
ALREADY_RAN = """

IMPORTANT — this turn has already been handled: {agents} {verb} already
answered the user's latest message, and {pronoun} reply is the most recent
assistant message above.

Answer FINISH unless the user explicitly asked for something that a
DIFFERENT specialist must do and that has not run yet. Re-running the same
specialist on the same message produces a duplicate answer. When in doubt,
FINISH."""


class Route(BaseModel):
    reasoning: str = Field(description="One sentence: why this route")
    next: Literal["activity_scout", "sleep_energy", "idea_generator",
                  "people_connector", "planner", "FINISH"]


def make_supervisor(llm):
    router = llm.with_structured_output(Route)

    def supervisor(state) -> Command:
        history = state.get("routing_history", [])
        if len(history) >= MAX_HOPS:
            return Command(goto=END)

        # structured-output retry (Phase 4): transient validation/API
        # failures get ONE retry; then fail closed with a human message
        # instead of a 500 mid-conversation.
        prompt = ROUTER_PROMPT
        if history:
            unique = list(dict.fromkeys(history))
            prompt += ALREADY_RAN.format(
                agents=" and ".join(unique),
                verb="have" if len(unique) > 1 else "has",
                pronoun="their" if len(unique) > 1 else "its")

        decision: Route | None = None
        for attempt in (1, 2):
            try:
                decision = router.invoke(
                    [SystemMessage(content=prompt), *state["messages"]])
                break
            except Exception:
                if attempt == 2:
                    return Command(goto=END, update={"messages": [AIMessage(
                        content="I'm having trouble processing that right now — "
                                "mind rephrasing or trying again in a moment?")]})

        if decision.next == "FINISH":
            return Command(goto=END)
        return Command(
            goto=decision.next,
            update={"routing_history": history + [decision.next]},
        )

    return supervisor
