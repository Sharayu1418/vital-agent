"""Phase 4 guardrail tests: crisis path, budget enforcement, usage
recording, supervisor retry. The crisis path is the one where a regression
is least acceptable — test it through the real API surface."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import guardrails
from vital.config import settings


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    settings.cache_clear()
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    yield
    settings.cache_clear()


# ---------- crisis detection ----------

@pytest.mark.parametrize("message", [
    "I want to kill myself",
    "lately I've been feeling suicidal",
    "I just want to end my life, nothing helps",
    "sometimes I think about hurting myself",
    "everyone would be better off without me",
])
def test_crisis_messages_detected(message):
    assert guardrails.crisis_check(message)


@pytest.mark.parametrize("message", [
    "this workout is brutal, my legs are dead",
    "I'm dying to try that pottery class",
    "I slept terribly and feel like a zombie",
    "kill the lights at 11pm, that's my plan",
])
def test_everyday_dark_phrasing_not_flagged(message):
    assert not guardrails.crisis_check(message)


def test_crisis_response_has_resources_without_guarantees():
    r = guardrails.CRISIS_RESPONSE
    assert "988" in r and "741741" in r
    for overclaim in ("confidential", "anonymous", "guarantee"):
        assert overclaim not in r.lower()


# ---------- budget ----------

def test_budget_counts_and_trips(monkeypatch):
    monkeypatch.setenv("DAILY_TOKEN_BUDGET", "100")
    settings.cache_clear()
    assert not guardrails.budget_exceeded("u1")
    guardrails.record_usage("u1", 60)
    assert not guardrails.budget_exceeded("u1")
    guardrails.record_usage("u1", 60)
    assert guardrails.budget_exceeded("u1")      # 120 >= 100
    assert not guardrails.budget_exceeded("u2")  # per-user


def test_estimate_tokens_floor():
    assert guardrails.estimate_tokens("") == 1
    assert guardrails.estimate_tokens("x" * 400) == 100


# ---------- API integration ----------

def _client(monkeypatch, graph):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api
    monkeypatch.setattr(api, "graph", graph)
    return TestClient(api.app)


class RecordingGraph:
    def __init__(self):
        self.calls = 0

    async def astream_events(self, _inputs, config=None, version=None):
        self.calls += 1
        return
        yield

    def get_state(self, _config):
        from types import SimpleNamespace
        return SimpleNamespace(tasks=(), values={"messages": [], "user_id": "u",
                                                 "routing_history": []})


def test_crisis_message_bypasses_graph(monkeypatch):
    fake = RecordingGraph()
    client = _client(monkeypatch, fake)
    r = client.post("/chat", json={"message": "I want to kill myself"})
    assert r.status_code == 200
    assert "988" in r.text
    assert fake.calls == 0          # no routing, no tools, no LLM


def test_budget_exhausted_returns_429(monkeypatch):
    monkeypatch.setenv("DAILY_TOKEN_BUDGET", "10")
    settings.cache_clear()
    fake = RecordingGraph()
    client = _client(monkeypatch, fake)
    # exhaust the anonymous session's budget: first request records usage
    first = client.post("/chat", json={"message": "hello there friend"})
    assert first.status_code == 200
    second = client.post("/chat", json={"message": "hello again"})
    assert second.status_code == 429
    assert fake.calls == 1


def test_approve_is_not_a_budget_bypass(monkeypatch):
    # an 'edit' resume re-invokes the planner LLM — /approve must check
    # the budget BEFORE resuming (review finding)
    monkeypatch.setenv("DAILY_TOKEN_BUDGET", "10")
    settings.cache_clear()
    fake = RecordingGraph()
    client = _client(monkeypatch, fake)
    client.post("/chat", json={"message": "hello there friend"})  # burns budget
    r = client.post("/approve", json={"action": "edit", "feedback": "more sleep"})
    assert r.status_code == 429  # not 409: budget fires before interrupt check
    assert fake.calls == 1       # resume never reached the graph


# ---------- supervisor retry ----------

def test_supervisor_retries_once_then_succeeds():
    from vital.supervisor import Route, make_supervisor

    class FlakyRouter:
        def __init__(self):
            self.calls = 0
        def invoke(self, _msgs):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("transient structured-output failure")
            return Route(reasoning="ok", next="sleep_energy")

    class LLM:
        def __init__(self):
            self.router = FlakyRouter()
        def with_structured_output(self, _s):
            return self.router

    llm = LLM()
    cmd = make_supervisor(llm)({"messages": [("user", "hi")], "routing_history": []})
    assert cmd.goto == "sleep_energy"
    assert llm.router.calls == 2


def test_supervisor_fails_closed_with_human_message():
    from langgraph.graph import END
    from vital.supervisor import make_supervisor

    class AlwaysBroken:
        def invoke(self, _msgs):
            raise ValueError("boom")

    class LLM:
        def with_structured_output(self, _s):
            return AlwaysBroken()

    cmd = make_supervisor(LLM())({"messages": [("user", "hi")], "routing_history": []})
    assert cmd.goto == END
    assert "trouble" in cmd.update["messages"][0].content
