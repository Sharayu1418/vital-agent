"""Regression: _aget_graph_state must prefer async state reads and fall back
to sync — NOT recurse (the prod hotfix called itself forever)."""
import asyncio
from types import SimpleNamespace

import pytest


def _helper_with(graph, monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    import vital.api as api
    monkeypatch.setattr(api, "graph", graph)
    return api._aget_graph_state


def test_prefers_async_when_available(monkeypatch):
    class AsyncGraph:
        async def aget_state(self, _config):
            return "async-state"

        def get_state(self, _config):  # must NOT be used when aget_state exists
            raise AssertionError("sync path used despite aget_state")

    helper = _helper_with(AsyncGraph(), monkeypatch)
    assert asyncio.run(helper({})) == "async-state"


def test_falls_back_to_sync_without_recursing(monkeypatch):
    class SyncOnlyGraph:
        def get_state(self, _config):
            return "sync-state"

    helper = _helper_with(SyncOnlyGraph(), monkeypatch)
    # the broken version recursed forever here (RecursionError)
    assert asyncio.run(helper({})) == "sync-state"


def test_async_endpoints_work_with_async_only_graph(monkeypatch):
    """thread_messages/debug/approve paths must survive a graph that has
    ONLY async state access (prod AsyncPostgresSaver shape)."""
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api

    class AsyncOnlyGraph:
        async def astream_events(self, *_a, **_k):
            return
            yield

        async def aget_state(self, _config):
            return SimpleNamespace(tasks=(), values={"messages": [
                SimpleNamespace(type="ai", content="hello from prod shape")]})

    monkeypatch.setattr(api, "graph", AsyncOnlyGraph())
    client = TestClient(api.app)

    r = client.get("/threads/t1/messages")
    assert r.status_code == 200
    assert r.json()["messages"] == [{"role": "ai", "text": "hello from prod shape"}]

    r = client.post("/chat", json={"message": "hi"})  # stream tail uses state too
    assert r.status_code == 200
    assert "hello from prod shape" in r.text
