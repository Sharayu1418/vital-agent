"""HITL planner flow tests — real interrupt/resume mechanics on a real
StateGraph with MemorySaver, fake LLM, real LocalCalendar + SQLite.
Covers: pause payload, approve→commit, edit→revise→approve, reject writes
nothing, idempotent double-commit, unknown action fails closed."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command

from vital import storage
from vital.calendar import LocalCalendar
from vital.planner import (WeekPlan, PlanItem, make_commit_plan, make_planner,
                           make_request_approval, plan_hash)
from vital.state import VitalState


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    from vital.config import settings
    settings.cache_clear()
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    yield
    settings.cache_clear()


PLAN_A = WeekPlan(items=[
    PlanItem(day="Saturday", start="10:00", end="12:00", title="Bouldering",
             kind="activity", rationale="high energy morning"),
    PlanItem(day="Saturday", start="23:00", end="23:30", title="Wind-down",
             kind="sleep", rationale="target bedtime"),
], tradeoffs="none")

PLAN_B = WeekPlan(items=[
    PlanItem(day="Sunday", start="10:00", end="12:00", title="Bouldering",
             kind="activity", rationale="moved per user request"),
], tradeoffs="moved to Sunday")


class FakePlannerLLM:
    def __init__(self, plans):
        self.plans = list(plans)
        self.prompts = []

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.prompts.append(str(messages))
        return self.plans.pop(0)


def make_graph(llm):
    builder = StateGraph(VitalState)
    builder.add_node("planner", make_planner(llm))
    builder.add_node("request_approval", make_request_approval())
    builder.add_node("commit_plan", make_commit_plan(LocalCalendar()))
    builder.add_edge(START, "planner")
    return builder.compile(checkpointer=MemorySaver())


CFG = {"configurable": {"thread_id": "t1"}}
INPUT = {"messages": [("user", "plan my weekend")], "user_id": "u1",
         "routing_history": []}


def test_graph_pauses_with_plan_payload():
    graph = make_graph(FakePlannerLLM([PLAN_A]))
    result = graph.invoke(INPUT, CFG)
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "plan_approval"
    assert payload["plan"]["items"][0]["title"] == "Bouldering"
    assert storage.calendar_events("u1") == []  # nothing written while paused


def test_approve_commits_events():
    graph = make_graph(FakePlannerLLM([PLAN_A]))
    graph.invoke(INPUT, CFG)
    result = graph.invoke(Command(resume={"action": "approve"}), CFG)
    events = storage.calendar_events("u1")
    assert len(events) == 2
    assert {e["title"] for e in events} == {"Bouldering", "Wind-down"}
    assert "2 events" in result["messages"][-1].content


def test_edit_revises_with_feedback_then_approve():
    llm = FakePlannerLLM([PLAN_A, PLAN_B])
    graph = make_graph(llm)
    graph.invoke(INPUT, CFG)
    mid = graph.invoke(Command(resume={"action": "edit",
                                       "feedback": "move climbing to Sunday"}), CFG)
    assert "__interrupt__" in mid                      # paused again on revision
    assert mid["__interrupt__"][0].value["plan"]["items"][0]["day"] == "Sunday"
    assert "move climbing to Sunday" in llm.prompts[1]  # feedback reached planner
    graph.invoke(Command(resume={"action": "approve"}), CFG)
    assert len(storage.calendar_events("u1")) == 1


def test_reject_writes_nothing():
    graph = make_graph(FakePlannerLLM([PLAN_A]))
    graph.invoke(INPUT, CFG)
    result = graph.invoke(Command(resume={"action": "reject"}), CFG)
    assert storage.calendar_events("u1") == []
    assert "discarded" in result["messages"][-1].content


def test_unknown_action_fails_closed():
    graph = make_graph(FakePlannerLLM([PLAN_A]))
    graph.invoke(INPUT, CFG)
    graph.invoke(Command(resume={"action": "yolo-commit"}), CFG)
    assert storage.calendar_events("u1") == []  # treated as reject


def test_double_commit_is_idempotent():
    cal = LocalCalendar()
    plan = PLAN_A.model_dump()
    h = plan_hash(plan)
    assert cal.commit("u1", plan, h) == 2
    assert cal.commit("u1", plan, h) == 0            # same hash: no-op
    assert len(storage.calendar_events("u1")) == 2
    assert cal.commit("u2", plan, h) == 2            # other user unaffected


def test_plan_hash_is_canonical():
    a = {"items": [{"x": 1}], "tradeoffs": "t"}
    b = {"tradeoffs": "t", "items": [{"x": 1}]}      # key order differs
    assert plan_hash(a) == plan_hash(b)
    assert plan_hash(a) != plan_hash({"items": [], "tradeoffs": "t"})
