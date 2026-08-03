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


# ---------- P0-5: real token accounting ----------

def _model_end(total=None, inp=None, out=None):
    """An on_chat_model_end event carrying LangChain-normalized usage."""
    from types import SimpleNamespace
    usage = {}
    if total is not None:
        usage["total_tokens"] = total
    if inp is not None:
        usage["input_tokens"] = inp
    if out is not None:
        usage["output_tokens"] = out
    return {"event": "on_chat_model_end",
            "data": {"output": SimpleNamespace(usage_metadata=usage)}}


def test_tokens_from_model_end_prefers_total():
    assert guardrails.tokens_from_model_end(_model_end(total=1234)) == 1234


def test_tokens_from_model_end_sums_input_and_output():
    assert guardrails.tokens_from_model_end(_model_end(inp=900, out=100)) == 1000


def test_tokens_from_model_end_reads_plain_dict_output():
    event = {"event": "on_chat_model_end",
             "data": {"output": {"usage_metadata": {"total_tokens": 77}}}}
    assert guardrails.tokens_from_model_end(event) == 77


def test_tokens_from_model_end_reads_llm_result_shape():
    from types import SimpleNamespace
    event = {"event": "on_chat_model_end", "data": {"output": SimpleNamespace(
        usage_metadata=None, llm_output={"token_usage": {"total_tokens": 42}})}}
    assert guardrails.tokens_from_model_end(event) == 42


def test_tokens_from_model_end_returns_zero_without_usage():
    """Fakes and providers with no usage metadata must fall back, not crash."""
    assert guardrails.tokens_from_model_end({"event": "on_chat_model_end",
                                             "data": {"output": None}}) == 0
    assert guardrails.tokens_from_model_end({"event": "on_chat_model_end",
                                             "data": {}}) == 0


def test_remaining_budget_floors_at_zero(monkeypatch):
    monkeypatch.setenv("DAILY_TOKEN_BUDGET", "100")
    settings.cache_clear()
    assert guardrails.remaining_budget("u9") == 100
    guardrails.record_usage("u9", 30)
    assert guardrails.remaining_budget("u9") == 70
    guardrails.record_usage("u9", 500)
    assert guardrails.remaining_budget("u9") == 0


class UsageGraph:
    """Emits streamed text plus per-call usage metadata, like Vertex does."""

    def __init__(self, per_call_tokens, calls=1):
        self.per_call_tokens = per_call_tokens
        self.calls = calls
        self.consumed = 0
        self.closed = False

    async def astream_events(self, _inputs, config=None, version=None):
        from types import SimpleNamespace
        try:
            for i in range(self.calls):
                self.consumed = i + 1
                yield {"event": "on_chat_model_stream",
                       "metadata": {"langgraph_node": "activity_scout"},
                       "data": {"chunk": SimpleNamespace(content=f"part{i} ")}}
                yield _model_end(total=self.per_call_tokens)
        finally:
            self.closed = True

    def get_state(self, _config):
        from types import SimpleNamespace
        return SimpleNamespace(tasks=(), values={"messages": [],
                                                 "routing_history": ["activity_scout"]})


def test_real_usage_is_billed_not_the_heuristic(monkeypatch):
    """The chars/4 heuristic would score this turn at a few tokens. The
    provider says 3 x 5000. The billed figure must be the provider's."""
    monkeypatch.setenv("DAILY_TOKEN_BUDGET", "1000000")
    settings.cache_clear()
    fake = UsageGraph(per_call_tokens=5000, calls=3)
    client = _client(monkeypatch, fake)
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 200
    from vital import storage
    user_id = f"anon-{client.cookies['vital_session']}"
    assert storage.tokens_used_today(user_id) == 15000


def test_turn_aborts_midway_once_the_budget_is_gone(monkeypatch):
    """Pre-turn checks alone let a single turn run unbounded. The third model
    call pushes past the cap, so the stream stops there instead of running
    all ten hops."""
    monkeypatch.setenv("DAILY_TOKEN_BUDGET", "2500")
    settings.cache_clear()
    fake = UsageGraph(per_call_tokens=1000, calls=10)
    client = _client(monkeypatch, fake)
    r = client.post("/chat", json={"message": "plan my whole month"})
    assert r.status_code == 200
    assert fake.consumed == 3            # stopped at the call that crossed 2500
    assert fake.closed                   # generator torn down, no leaked task
    assert "usage limit" in r.text
    assert "part0" in r.text             # partial answer preserved, not clobbered


def test_heuristic_still_used_when_provider_reports_no_usage(monkeypatch):
    monkeypatch.setenv("DAILY_TOKEN_BUDGET", "1000000")
    settings.cache_clear()
    fake = RecordingGraph()
    client = _client(monkeypatch, fake)
    client.post("/chat", json={"message": "x" * 400})
    from vital import storage
    user_id = f"anon-{client.cookies['vital_session']}"
    assert storage.tokens_used_today(user_id) > 0
