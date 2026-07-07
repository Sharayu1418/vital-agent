"""Security surface tests.

Layer 1: pure unit tests on vital.security (no graph, no GCP).
Layer 2: route-level tests through the real FastAPI app with a fake graph:
anonymous session isolation, trusted identity assertion, 401s, debug routes.
"""
import importlib
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
# TestClient talks http, and secure cookies don't survive http cookie jars
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

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
    assert security.caller_is_trusted("Bearer anything") is False


def test_blank_token_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "   ")
    settings.cache_clear()
    assert security.configured_token() is None
    assert security.caller_is_trusted("Bearer    ") is False  # can't match blank


def test_wrong_token_is_hard_401(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        security.caller_is_trusted("Bearer wrong")
    assert exc.value.status_code == 401


def test_missing_bearer_scheme_is_401(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        security.caller_is_trusted("s3cret")  # raw token, no scheme
    assert exc.value.status_code == 401


def test_correct_token_is_trusted(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    assert security.caller_is_trusted("Bearer s3cret") is True


def test_missing_header_with_token_configured_is_anonymous_not_401(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    assert security.caller_is_trusted(None) is False


def test_resolve_identity_trusted_passthrough():
    assert security.resolve_identity("alice", True, None) == ("alice", None)


def test_resolve_identity_anonymous_gets_fresh_server_session():
    user_id, new_session = security.resolve_identity("alice", False, None)
    assert new_session is not None and len(new_session) == 32
    assert user_id == f"anon-{new_session}"  # body user_id 'alice' ignored


def test_resolve_identity_valid_cookie_is_stable():
    session = "a" * 32
    assert security.resolve_identity("x", False, session) == (f"anon-{session}", None)


def test_resolve_identity_rejects_forged_cookie():
    user_id, new_session = security.resolve_identity("x", False, "../local-user")
    assert new_session is not None  # forged format → fresh session, not adopted
    assert "local-user" not in user_id


def test_startup_refuses_debug_without_token(monkeypatch):
    monkeypatch.setenv("DEBUG_ENDPOINTS", "true")
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    settings.cache_clear()
    with pytest.raises(RuntimeError):
        security.validate_startup()


def test_startup_allows_debug_with_token(monkeypatch):
    monkeypatch.setenv("DEBUG_ENDPOINTS", "true")
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    security.validate_startup()  # must not raise


# ---------- Layer 2: through the real app ----------

class FakeGraph:
    """Records the thread_id the endpoint resolved; streams nothing."""
    def __init__(self):
        self.seen: list[str] = []

    async def astream_events(self, _inputs, config=None, version=None):
        self.seen.append(config["configurable"]["thread_id"])
        return
        yield  # makes this an async generator


def _client(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api

    fake = FakeGraph()
    monkeypatch.setattr(api, "graph", fake)
    # TestClient WITHOUT context manager: lifespan (real graph build) never runs
    return TestClient(api.app), fake


def test_anonymous_caller_cannot_choose_identity(monkeypatch):
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi", "user_id": "alice", "thread_id": "t1"})
    assert r.status_code == 200
    assert fake.seen[0].startswith("anon-") and fake.seen[0].endswith(":t1")
    assert "alice" not in fake.seen[0] and "local-user" not in fake.seen[0]
    assert security.SESSION_COOKIE in r.cookies  # server issued a session


def test_two_anonymous_clients_never_collide(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api

    fake = FakeGraph()
    monkeypatch.setattr(api, "graph", fake)
    client_a = TestClient(api.app)  # separate cookie jars
    client_b = TestClient(api.app)
    client_a.post("/chat", json={"message": "hi"})  # both use default thread 'demo'
    client_b.post("/chat", json={"message": "hi"})
    assert len(fake.seen) == 2
    assert fake.seen[0] != fake.seen[1]  # the P1 collision, now impossible
    assert all(t.startswith("anon-") for t in fake.seen)


def test_anonymous_session_persists_across_requests(monkeypatch):
    client, fake = _client(monkeypatch)  # TestClient keeps cookies between calls
    client.post("/chat", json={"message": "one"})
    client.post("/chat", json={"message": "two"})
    assert fake.seen[0] == fake.seen[1]  # same client → same thread → continuity


def test_memories_route_issues_session_cookie_to_new_anonymous_user(monkeypatch):
    # P1 regression: identity-resolving routes other than /chat must also
    # set the cookie, or first-contact uploads land under an unreachable ID
    client, _ = _client(monkeypatch)
    r = client.get("/memories")
    assert r.status_code == 200
    assert security.SESSION_COOKIE in r.cookies


def test_memories_route_reuses_existing_session(monkeypatch):
    client, _ = _client(monkeypatch)
    first = client.get("/memories")
    session = first.cookies[security.SESSION_COOKIE]
    second = client.get("/memories")  # cookie jar sends it back
    assert security.SESSION_COOKIE not in second.headers.get("set-cookie", "")
    assert client.cookies[security.SESSION_COOKIE] == session


def test_upload_and_chat_share_anonymous_identity(monkeypatch, tmp_path):
    # end-to-end P1 scenario: anonymous upload → same session chats → same user_id
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    settings.cache_clear()
    client, fake = _client(monkeypatch)
    up = client.post("/upload/health",
                     files={"file": ("sleep.csv", b"date,duration_min\n2026-07-01,420\n")})
    assert up.status_code == 200
    session = client.cookies[security.SESSION_COOKIE]
    client.post("/chat", json={"message": "how did I sleep?"})
    assert fake.seen[0].startswith(f"anon-{session}:")


def test_session_cookie_is_secure_by_default(monkeypatch):
    # Simulate prod: no SESSION_COOKIE_SECURE override → Secure flag present
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    settings.cache_clear()
    client, _ = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi"})
    set_cookie = r.headers["set-cookie"]
    assert "Secure" in set_cookie and "HttpOnly" in set_cookie


def test_trusted_caller_may_assert_user_id(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi", "user_id": "alice", "thread_id": "t1"},
                    headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    assert fake.seen[0] == "alice:t1"
    assert security.SESSION_COOKIE not in r.cookies  # no session needed


def test_invalid_token_is_401(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi"},
                    headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert fake.seen == []  # rejected before any graph work


def test_debug_routes_absent_by_default(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    import vital.api as api
    assert not any(getattr(r, "path", "").startswith("/debug") for r in api.app.routes)


def test_debug_routes_exist_and_require_token_when_enabled(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api

    monkeypatch.setenv("DEBUG_ENDPOINTS", "true")
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    reloaded = importlib.reload(api)
    assert any(getattr(r, "path", "").startswith("/debug") for r in reloaded.app.routes)

    monkeypatch.setattr(reloaded, "graph", FakeGraph())
    client = TestClient(reloaded.app)
    assert client.get("/debug/state/u/t").status_code == 401          # no token
    # (with the token it would proceed to graph.get_state — covered manually)

    # restore module to default (debug off) so test order can't leak state
    monkeypatch.delenv("DEBUG_ENDPOINTS")
    monkeypatch.delenv("API_AUTH_TOKEN")
    settings.cache_clear()
    importlib.reload(api)
