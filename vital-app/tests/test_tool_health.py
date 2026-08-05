"""Tool health observation.

Written after the Reddit integration failed on every single call for months
without anyone noticing. It degraded gracefully — an `error` key, which the
agent reported as "temporarily down" — so a permanently dead feature was
indistinguishable from a blip. Graceful degradation without a failure RATE
hides outages indefinitely.

These tests cover the two properties that make the alerting trustworthy:
the outcome classifier is right, and EVERY tool is observed centrally so a
new one can't be forgotten.
"""
import json
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from vital import metrics


class _Capture(logging.Handler):
    """Collect metric lines straight off vital.metrics.

    Not caplog: it attaches to both the named logger and root, so every line
    was counted twice and `assert len(lines) == 1` failed for the wrong
    reason. A dedicated handler is deterministic.
    """

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(json.loads(record.getMessage()))


@contextmanager
def capture_metrics():
    handler = _Capture()
    metrics.logger.addHandler(handler)
    try:
        yield handler.lines
    finally:
        metrics.logger.removeHandler(handler)


# ---------- the classifier ----------

def test_a_dict_with_an_error_key_is_a_failure():
    outcome, detail = metrics.tool_outcome({"error": "venue search unavailable"})
    assert outcome == "error"
    assert "venue search unavailable" in detail


def test_a_normal_result_is_success():
    assert metrics.tool_outcome({"venues": [{"name": "The Court Club"}]}) == ("ok", None)
    assert metrics.tool_outcome({"communities": []}) == ("ok", None)


def test_no_data_is_not_an_error():
    """analyze_sleep_data returns no_data when the user simply hasn't
    uploaded anything. Nothing is broken; counting it as a failure would
    inflate the very rate we alert on."""
    assert metrics.tool_outcome({"no_data": "nothing uploaded yet"})[0] == "ok"


def test_non_dict_returns_are_treated_as_success():
    """Deliberately conservative: inventing failures poisons the rate."""
    assert metrics.tool_outcome("some string")[0] == "ok"
    assert metrics.tool_outcome(None)[0] == "ok"
    assert metrics.tool_outcome([1, 2, 3])[0] == "ok"


def test_an_empty_error_value_is_not_a_failure():
    assert metrics.tool_outcome({"error": ""})[0] == "ok"


def test_error_detail_is_truncated():
    outcome, detail = metrics.tool_outcome({"error": "x" * 500})
    assert outcome == "error"
    assert len(detail) <= 200


# ---------- the log line ----------

def test_log_tool_emits_parseable_json_with_the_fields_alerts_need():
    with capture_metrics() as lines:
        metrics.log_tool("u1", "search_places", "error",
                         error="HTTPStatusError", duration_ms=1234)
    assert len(lines) == 1
    line = lines[0]
    assert line["metric"] == "tool_call"      # the log-based metric filter
    assert line["tool"] == "search_places"    # grouped by, to spot ONE dead tool
    assert line["outcome"] == "error"
    assert line["error"] == "HTTPStatusError"
    assert line["duration_ms"] == 1234


def test_log_tool_never_logs_raw_identity():
    """Anonymous session ids are identity too."""
    with capture_metrics() as lines:
        metrics.log_tool("anon-deadbeef", "get_weather", "ok")
    assert "anon-deadbeef" not in json.dumps(lines[0])
    assert lines[0]["user"] != "anon-deadbeef"


# ---------- central observation: every tool, no exceptions ----------

class ToolGraph:
    """Emits the tool events astream_events produces around a tool call."""

    def __init__(self, tool_name, output):
        self.tool_name = tool_name
        self.output = output

    async def astream_events(self, _inputs, config=None, version=None):
        yield {"event": "on_tool_start", "run_id": "r1", "name": self.tool_name,
               "metadata": {"langgraph_node": "activity_scout"}, "data": {}}
        yield {"event": "on_tool_end", "run_id": "r1", "name": self.tool_name,
               "metadata": {"langgraph_node": "activity_scout"},
               "data": {"output": self.output}}

    def get_state(self, _config):
        return SimpleNamespace(tasks=(), values={"messages": [],
                                                 "routing_history": []})


def _client(monkeypatch, graph):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api
    monkeypatch.setattr(api, "graph", graph)
    return TestClient(api.app)


def _tool_lines(lines):
    return [line for line in lines if line.get("metric") == "tool_call"]


def test_a_failing_tool_is_logged_as_an_error(monkeypatch):
    """The Reddit case: the tool answered politely, the feature was dead."""
    with capture_metrics() as captured:
        client = _client(monkeypatch, ToolGraph(
            "search_places", {"error": "venue search unavailable (HTTPError)"}))
        client.post("/chat", json={"message": "find me a climbing gym"})

    lines = _tool_lines(captured)
    assert len(lines) == 1
    assert lines[0]["tool"] == "search_places"
    assert lines[0]["outcome"] == "error"
    assert "HTTPError" in lines[0]["error"]


def test_a_working_tool_is_logged_as_ok_with_a_duration(monkeypatch):
    with capture_metrics() as captured:
        client = _client(monkeypatch, ToolGraph(
            "search_places", {"venues": [{"name": "The Court Club"}]}))
        client.post("/chat", json={"message": "find me a climbing gym"})

    lines = _tool_lines(captured)
    assert len(lines) == 1
    assert lines[0]["outcome"] == "ok"
    assert "duration_ms" in lines[0], "latency per tool is half the point"


def test_every_tool_reports_failure_the_same_way():
    """Central observation only works if the contract is uniform. A tool
    that signals failure some other way is invisible to the alerting —
    which is exactly what analyze_sleep_data used to be, returning a bare
    string while the paid E2B sandbox failed behind it."""
    import inspect

    from vital.agents.people_connector import find_activity_buddies
    from vital.agents.sleep_energy import analyze_sleep_data
    from vital.tools.events import search_events
    from vital.tools.places import search_places
    from vital.tools.weather import get_weather

    for tool in (get_weather, search_places, search_events,
                 find_activity_buddies, analyze_sleep_data):
        returns = inspect.signature(tool.func).return_annotation
        assert returns is dict, (
            f"{tool.name} returns {returns}, not dict — it cannot signal "
            "failure via an 'error' key, so tool-health alerting will never "
            "see it fail")
