"""Security surface tests — the review's high-severity finding, now covered.

Layer 1: pure unit tests on vital.security (no graph, no GCP).
Layer 2: route-level tests through the real FastAPI app with a fake graph,
covering anonymous pinning, 401 on bad token, and debug-route absence.
"""
import importlib
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest
from fastapi import HTTPException

from vital import security
from vital.config import settings


@pytest.fixture(autouse=True)
def fresh_settings():
    settings.cache_clear()
    yield
    settings.cache_clear()


# ---------- Layer 1: unit ----------

def test_no_token_configured_means_anonymous():
    assert security.caller_is_trusted(None) is False
    # even a header can't earn trust when no token is configured to verify it
    assert security.caller_is_trusted("Bearer anything") is False


def test_wrong_token_is_hard_401(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        security.caller_is_trusted("Bearer wrong")
    assert exc.value.status_code == 401


def test_correct_token_is_trusted(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    assert security.caller_is_trusted("Bearer s3cret") is True


def test_missing_header_with_token_configured_is_anonymous_not_401(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    assert security.caller_is_trusted(None) is False


def test_resolve_user_id_pins_untrusted_callers():
    assert security.resolve_user_id("alice", trusted=False) == "local-user"
    assert security.resolve_user_id("alice", trusted=True) == "alice"


# ---------- Layer 2: through the real app ----------

class FakeGraph:
    """Records the thread_id the endpoint resolved; streams nothing."""
    def __init__(self):
        self.seen: dict = {}

    async def astream_events(self, _inputs, config=None, version=None):
        self.seen["thread_id"] = config["configurable"]["thread_id"]
        return
        yield  # makes this an async generator


@pytest.fixture
def client_and_fake(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api

    fake = FakeGraph()
    monkeypatch.setattr(api, "graph", fake)
    # TestClient WITHOUT context manager: lifespan (real graph build) never runs
    return TestClient(api.app), fake


def test_anonymous_caller_is_pinned_regardless_of_body(client_and_fake):
    client, fake = client_and_fake
    r = client.post("/chat", json={"message": "hi", "user_id": "alice", "thread_id": "t1"})
    assert r.status_code == 200
    assert fake.seen["thread_id"] == "local-user:t1"  # spoof attempt neutralized


def test_trusted_caller_may_assert_user_id(client_and_fake, monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    client, fake = client_and_fake
    r = client.post("/chat", json={"message": "hi", "user_id": "alice", "thread_id": "t1"},
                    headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    assert fake.seen["thread_id"] == "alice:t1"


def test_invalid_token_is_401(client_and_fake, monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    client, fake = client_and_fake
    r = client.post("/chat", json={"message": "hi"},
                    headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert "thread_id" not in fake.seen  # rejected before any graph work


def test_debug_routes_absent_by_default():
    pytest.importorskip("langchain_google_vertexai")
    import vital.api as api
    assert not any(getattr(r, "path", "").startswith("/debug") for r in api.app.routes)


def test_debug_routes_exist_only_when_enabled(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    import vital.api as api

    monkeypatch.setenv("DEBUG_ENDPOINTS", "true")
    settings.cache_clear()
    reloaded = importlib.reload(api)
    assert any(getattr(r, "path", "").startswith("/debug") for r in reloaded.app.routes)

    # restore module to default (debug off) so test order can't leak state
    monkeypatch.delenv("DEBUG_ENDPOINTS")
    settings.cache_clear()
    importlib.reload(api)
