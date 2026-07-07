"""Regression: never stream raw provider content objects (Gemini content
blocks carry internals like thought_signature that must not reach the UI)."""
from types import SimpleNamespace

import pytest

from vital.api import visible_text

BLOCK = [{"type": "text", "text": "hello", "thought_signature": "secret"}]


# ---------- unit: the extractor ----------

@pytest.mark.parametrize("content,expected", [
    ("plain string", "plain string"),
    (None, ""),
    (BLOCK, "hello"),
    ([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], "ab"),
    ({"type": "text", "text": "solo dict"}, "solo dict"),
    ({"no_text_key": True}, ""),
    ([{"type": "thinking", "thought_signature": "x"}], ""),  # no text field at all
    (SimpleNamespace(text="attr text"), "attr text"),
    (SimpleNamespace(other="y"), ""),
    ([BLOCK[0], "and raw string"], "helloand raw string"),
])
def test_visible_text(content, expected):
    assert visible_text(content) == expected


# ---------- integration: through the SSE stream ----------

class BlockStreamingGraph:
    """Streams one Gemini-style content-block chunk from a user-facing node."""
    async def astream_events(self, _inputs, config=None, version=None):
        yield {"event": "on_chat_model_stream",
               "metadata": {"langgraph_node": "sleep_energy"},
               "data": {"chunk": SimpleNamespace(content=BLOCK)}}

    def get_state(self, _config):
        return SimpleNamespace(tasks=(), values={"messages": [], "routing_history": []})


class BlockFinalMessageGraph:
    """Final state message whose content is a block list (approve path)."""
    async def astream_events(self, _inputs, config=None, version=None):
        return
        yield

    def get_state(self, _config):
        return SimpleNamespace(tasks=(), values={
            "messages": [SimpleNamespace(type="ai", content=[
                {"type": "text", "text": "Done — 2 events on your calendar.",
                 "thought_signature": "secret"}])],
            "routing_history": []})


def _client(monkeypatch, graph):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api
    monkeypatch.setattr(api, "graph", graph)
    return TestClient(api.app)


def test_streamed_blocks_show_text_only(monkeypatch):
    client = _client(monkeypatch, BlockStreamingGraph())
    r = client.post("/chat", json={"message": "I want a hobby"})
    assert r.status_code == 200
    assert "hello" in r.text
    assert "thought_signature" not in r.text
    assert "secret" not in r.text
    assert "[{" not in r.text  # no stringified object soup


def test_final_message_blocks_show_text_only(monkeypatch):
    client = _client(monkeypatch, BlockFinalMessageGraph())
    r = client.post("/chat", json={"message": "approve"})
    assert "Done — 2 events on your calendar." in r.text
    assert "thought_signature" not in r.text and "secret" not in r.text
