"""Phase 5 feedback loop + CORS tests."""
import pytest

from vital import storage
from vital.config import settings


def _client(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api

    class NullGraph:
        async def astream_events(self, *_a, **_k):
            return
            yield

        def get_state(self, _c):
            from types import SimpleNamespace
            return SimpleNamespace(tasks=(), values={})

    monkeypatch.setattr(api, "graph", NullGraph())
    return TestClient(api.app)


def test_feedback_stored_under_session_identity(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/feedback", json={"rating": "up", "comment": "loved the plan",
                                       "thread_id": "t1"})
    assert r.status_code == 200
    day = storage.feedback_summary()["by_day"]
    assert day[0]["rating"] == "up" and day[0]["n"] == 1


def test_feedback_rejects_bad_rating(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/feedback", json={"rating": "meh"})
    assert r.status_code == 422


def test_cors_allows_configured_frontend_origin(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:3000")
    settings.cache_clear()
    client = _client(monkeypatch)
    r = client.options("/chat", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
    })
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_cors_blocks_other_origins(monkeypatch):
    client = _client(monkeypatch)
    r = client.options("/chat", headers={
        "Origin": "https://evil.example",
        "Access-Control-Request-Method": "POST",
    })
    assert r.headers.get("access-control-allow-origin") != "https://evil.example"
