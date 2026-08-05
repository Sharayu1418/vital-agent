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


# ---------- the production routing loop ----------

class SpyRouter:
    """Captures the prompt the router actually receives."""

    def __init__(self, next_):
        self.next_ = next_
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages[0].content)
        return Route(reasoning="fake", next=self.next_)


class SpyLLM:
    def __init__(self, next_):
        self.router = SpyRouter(next_)

    def with_structured_output(self, _schema):
        return self.router


def test_first_hop_prompt_says_nothing_about_prior_agents():
    llm = SpyLLM("sleep_energy")
    make_supervisor(llm)(_state())
    assert "already" not in llm.router.prompts[0].lower()


def test_router_is_told_which_agents_already_answered():
    """The production bug, in one assertion.

    routing_hops sat at MAX_HOPS on every real turn: the router saw only the
    message list, which does not distinguish an agent's reply from any other
    assistant message, so it re-read the same user message and routed to the
    same specialist again. It has to be TOLD.
    """
    llm = SpyLLM("FINISH")
    make_supervisor(llm)(_state(history=["sleep_energy"]))
    prompt = llm.router.prompts[0]
    assert "sleep_energy" in prompt
    assert "already" in prompt.lower()
    assert "FINISH" in prompt


def test_multiple_prior_agents_read_naturally(monkeypatch):
    # MAX_HOPS is 2, so a two-agent history hits the guard before the router
    # is ever called. Raise it here to reach the formatting code itself.
    import vital.supervisor as sup_mod
    monkeypatch.setattr(sup_mod, "MAX_HOPS", 5)
    llm = SpyLLM("FINISH")
    make_supervisor(llm)(_state(history=["sleep_energy", "activity_scout"]))
    # scope to the appended block, and flatten the wrapping
    tail = " ".join(llm.router.prompts[0].split("IMPORTANT")[-1].split())
    assert "sleep_energy and activity_scout have already answered" in tail


def test_repeated_hops_are_not_listed_twice(monkeypatch):
    import vital.supervisor as sup_mod
    monkeypatch.setattr(sup_mod, "MAX_HOPS", 5)
    llm = SpyLLM("FINISH")
    make_supervisor(llm)(_state(history=["sleep_energy"] * 3))
    # count within the appended block only: the agent list and few-shots in
    # the base prompt legitimately mention sleep_energy too
    tail = " ".join(llm.router.prompts[0].split("IMPORTANT")[-1].split())
    assert tail.count("sleep_energy") == 1
    assert "has already answered" in tail


def test_hop_guard_is_tight_enough_to_bound_cost():
    """MAX_HOPS was 5 and production hit it every turn — 5 agent runs per
    message. The guard is a backstop, not a budget."""
    assert MAX_HOPS <= 2
