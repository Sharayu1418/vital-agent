"""Planner + human-in-the-loop approval (Phase 3A).

The canonical LangGraph HITL pattern:

    planner → request_approval ──interrupt()── graph PAUSES, checkpointed
                    │  resume {"action": ...}
                    ├─ approve → commit_plan → END
                    ├─ edit    → planner (with feedback) → approval again
                    └─ reject  → END, nothing written

Security is topology (D11): commit_plan has NO other inbound edge, so no
prompt injection can reach the calendar without a human resume. The pause
is durable — with Postgres checkpoints it survives restarts and can wait
days.
"""
import hashlib
import json
from typing import Literal

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field


class PlanItem(BaseModel):
    day: str = Field(description="e.g. 'Saturday' or '2026-07-11'")
    start: str = Field(description="HH:MM 24h")
    end: str = Field(description="HH:MM 24h")
    title: str
    kind: Literal["sleep", "activity", "social", "idea_work"]
    rationale: str = Field(description="one line: why this, why here")


class WeekPlan(BaseModel):
    items: list[PlanItem]
    tradeoffs: str = Field(description="e.g. 'moved climbing to Sunday because rain Saturday'")


PLANNER_PROMPT = """You are VITAL's Planner. Synthesize everything known from \
this conversation — sleep targets, chosen activities, saved ideas, community \
events — into one concrete schedule.

Rules:
- Sleep first: if a target bedtime was discussed, it's a plan item, not a suggestion.
- Every item gets a rationale tied to something the user actually said.
- Realistic transitions (no back-to-back across town).
- 3-8 items. Fewer good items beat a packed grid.
- State tradeoffs honestly in the tradeoffs field."""

FORECAST_RULE = """

ENERGY FORECAST — place demanding items against it.
{forecast}

Rules for using it:
- Put the most demanding item of each day nearest a predicted peak, and
  keep low-effort or restful items near a dip. Say so in the rationale,
  with the actual predicted time: "climbing at 14:00, your predicted peak".
- Never contradict the forecast without naming the tradeoff.
- Confidence is {confidence}. Below 0.4 this is a population curve, not
  theirs — use it to order the day, but do not present the times as facts
  about them, and never invent precision the number does not support."""


def forecast_block() -> str:
    """The forecast, rendered for the planner's prompt.

    Injected rather than offered as a tool: the planner is a structured
    output call, and giving one model both a tool loop and a strict schema
    is a reliable way to get neither. Failure is silent on purpose — a
    forecasting outage should cost the plan its energy justification, not
    stop the user planning their weekend.
    """
    try:
        from vital import forecast as engine
        from vital import storage
        from vital.agents.sleep_energy import summarize

        nights = engine.nights_from_rows(
            storage.sleep_history(engine.DEBT_WINDOW_NIGHTS * 2),
            storage.health_rows(storage.current_user_id.get()))
        result = engine.forecast(nights, storage.local_now(), horizon_hours=72)
        summary = summarize(result)
    except Exception:
        return ""

    peak, dip = summary.get("peak"), summary.get("dip")
    if not peak or not dip:
        return ""
    lines = [f"Basis: {summary['basis']}",
             f"Predicted peak: {peak['at']} ({peak['energy']}) — {peak['why'][0]}",
             f"Predicted dip:  {dip['at']} ({dip['energy']}) — {dip['why'][0]}",
             f"Typical wake {summary['typical_wake']}, "
             f"bedtime {summary['typical_bedtime']}"]
    if summary["sleep_debt_hours"] > 2:
        lines.append(f"Carrying {summary['sleep_debt_hours']}h of sleep debt — "
                     "protect recovery before adding load.")
    return FORECAST_RULE.format(forecast="\n".join(lines),
                                confidence=summary["confidence"])


def plan_hash(plan: dict) -> str:
    """Canonical hash for idempotent commits (double-click protection)."""
    return hashlib.sha256(
        json.dumps(plan, sort_keys=True).encode()).hexdigest()[:16]


def make_planner(llm):
    structured = llm.with_structured_output(WeekPlan)

    def planner(state) -> Command:
        prompt = PLANNER_PROMPT + forecast_block()
        edit = state.get("edit_request")
        if edit:
            prompt += (f"\n\nThe user reviewed your previous draft and asked: "
                       f"'{edit}'. Revise accordingly; keep what they didn't question.")
        plan = structured.invoke([SystemMessage(content=prompt), *state["messages"]])

        # An empty plan must never reach the approval gate. Asked to "plan my
        # weekend" in a fresh thread with nothing discussed, the model
        # correctly has nothing to synthesise and returns zero items — which
        # rendered as a plan card with no rows and two buttons, and, once
        # approved, committed an empty plan whose hash then made every later
        # attempt report "that plan was already committed".
        if not plan.items:
            return Command(goto=END, update={"plan_draft": None, "messages": [
                AIMessage(content=(
                    "I don't have enough to work with yet — we haven't talked "
                    "about what you actually want to do. Tell me what you're "
                    "hoping to fit in (or ask me for ideas first), and I'll "
                    "build the schedule around it."))]})

        return Command(goto="request_approval",
                       update={"plan_draft": plan.model_dump(), "edit_request": None})
    return planner


def make_request_approval():
    def request_approval(state) -> Command:
        # interrupt() payload must be JSON-serializable (plain dict, not Pydantic)
        decision = interrupt({"type": "plan_approval", "plan": state["plan_draft"]})
        action = (decision or {}).get("action")
        if action == "approve":
            return Command(goto="commit_plan")
        if action == "edit":
            return Command(goto="planner",
                           update={"edit_request": (decision or {}).get("feedback", "")})
        # reject (or anything unrecognized: fail closed — write nothing)
        return Command(goto=END, update={
            "plan_draft": None,
            "messages": [AIMessage(content="No problem — I've discarded that plan. "
                                   "Tell me what to change if you want another take.")]})
    return request_approval


def make_commit_plan(calendar):
    """calendar: any object with .commit(user_id, plan: dict, plan_hash: str) -> int
    (number of events created; 0 when this hash was already committed)."""
    def commit_plan(state) -> Command:
        plan = state["plan_draft"]
        h = plan_hash(plan)
        created = calendar.commit(state["user_id"], plan, h)
        if created:
            msg = (f"Done — {created} events on your calendar. "
                   f"Tradeoffs I made: {plan.get('tradeoffs', 'none')}")
        else:
            msg = "That plan was already committed — no duplicate events created."
        return Command(goto=END, update={"plan_draft": None,
                                         "messages": [AIMessage(content=msg)]})
    return commit_plan
