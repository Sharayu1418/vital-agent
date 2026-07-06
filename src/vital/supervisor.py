"""Supervisor: routes each turn to a sub-agent via structured output.

Routing is classification → Flash model (D5). Quality lives in the few-shots
below; when routing evals fail, fix THIS prompt, don't upgrade the model.
"""
from typing import Literal

from langchain_core.messages import SystemMessage
from langgraph.graph import END
from langgraph.types import Command
from pydantic import BaseModel, Field

MAX_HOPS = 5  # loop guard: force finish after 5 agent hops in one turn

ROUTER_PROMPT = """You route user messages to the right VITAL agent.

Agents:
- activity_scout: finding things to DO — activities, venues, events, weekend plans
- sleep_energy: sleep quality, tiredness, energy management, logging sleep
- idea_generator: directionless energy, wanting projects/hobbies/purpose
- FINISH: user is done, says thanks, or the last agent reply fully answered them

Examples:
"I'm exhausted lately" -> sleep_energy
"bored, what should I do this weekend" -> activity_scout
"I have energy but no direction" -> idea_generator
"I'm tired of being bored" -> activity_scout  (boredom is the complaint, not sleep)
"slept 4 hours, still want to go out tonight" -> sleep_energy  (health first)
"thanks, that's all" -> FINISH

Route based on the LATEST user message in context of the conversation.
If a sub-agent just fully answered and no new user input is needed, FINISH."""


class Route(BaseModel):
    reasoning: str = Field(description="One sentence: why this route")
    next: Literal["activity_scout", "sleep_energy", "idea_generator", "FINISH"]


def make_supervisor(llm):
    router = llm.with_structured_output(Route)

    def supervisor(state) -> Command:
        history = state.get("routing_history", [])
        if len(history) >= MAX_HOPS:
            return Command(goto=END)

        decision: Route = router.invoke(
            [SystemMessage(content=ROUTER_PROMPT), *state["messages"]]
        )
        if decision.next == "FINISH":
            return Command(goto=END)
        return Command(
            goto=decision.next,
            update={"routing_history": history + [decision.next]},
        )

    return supervisor
