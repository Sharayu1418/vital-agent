"""Local supervisor tests with a fake LLM — no network, no GCP.

Covers the control flow that live routing evals can't cheaply cover:
FINISH, valid routes, and the MAX_HOPS loop guard.
"""
from langgraph.graph import END

from vital.supervisor import MAX_HOPS, Route, make_supervisor


class FakeRouter:
    def __init__(self, next_: str):
        self.next_ = next_
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return Route(reasoning="fake", next=self.next_)


class FakeLLM:
    def __init__(self, next_: str):
        self.router = FakeRouter(next_)

    def with_structured_output(self, _schema):
        return self.router


def _state(history: list[str] | None = None) -> dict:
    return {"messages": [("user", "hi")], "routing_history": history or []}


def test_valid_route_goes_to_agent_and_records_hop():
    sup = make_supervisor(FakeLLM("sleep_energy"))
    cmd = sup(_state())
    assert cmd.goto == "sleep_energy"
    assert cmd.update["routing_history"] == ["sleep_energy"]


def test_finish_goes_to_end():
    sup = make_supervisor(FakeLLM("FINISH"))
    assert sup(_state()).goto == END


def test_max_hops_forces_end_without_calling_llm():
    llm = FakeLLM("activity_scout")
    sup = make_supervisor(llm)
    cmd = sup(_state(history=["activity_scout"] * MAX_HOPS))
    assert cmd.goto == END
    assert llm.router.calls == 0  # loop guard fires before any model call


def test_route_schema_rejects_unknown_agent():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Route(reasoning="x", next="calendar_writer")  # not a wired agent (D11)
