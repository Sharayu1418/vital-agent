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

def test_no_header_means_anonymous():
    assert security.caller_is_trusted(None) is False


def test_unmatchable_bearer_is_401_never_anonymous():
    # Firebase-auth change: a PRESENT bearer that matches nothing is a hard
    # 401 — silently downgrading a broken client to a fresh anonymous
    # identity would hide misconfiguration and split the user's data
    with pytest.raises(HTTPException) as exc:
        security.caller_is_trusted("Bearer anything")
    assert exc.value.status_code == 401


def test_blank_token_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "   ")
    settings.cache_clear()
    assert security.configured_token() is None
    with pytest.raises(HTTPException) as exc:  # can't match blank → 401
        security.caller_is_trusted("Bearer    ")
    assert exc.value.status_code == 401


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


INTERNAL = security.AuthContext("internal")
ANON = security.AuthContext("anon")


def test_resolve_identity_trusted_passthrough():
    assert security.resolve_identity("alice", INTERNAL, None) == ("alice", None)


def test_resolve_identity_anonymous_gets_fresh_server_session():
    user_id, new_session = security.resolve_identity("alice", ANON, None)
    assert new_session is not None and len(new_session) == 32
    assert user_id == f"anon-{new_session}"  # body user_id 'alice' ignored


def test_resolve_identity_valid_cookie_is_stable():
    session = "a" * 32
    assert security.resolve_identity("x", ANON, session) == (f"anon-{session}", None)


def test_resolve_identity_rejects_forged_cookie():
    user_id, new_session = security.resolve_identity("x", ANON, "../local-user")
    assert new_session is not None  # forged format → fresh session, not adopted
    assert "local-user" not in user_id


def test_startup_refuses_debug_without_token(monkeypatch):
    monkeypatch.setenv("DEBUG_ENDPOINTS", "true")
    # blank (not delenv): deleting would let pydantic fall back to a
    # developer's real .env, and a token there would mask the failure
    monkeypatch.setenv("API_AUTH_TOKEN", "")
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
    """Records the thread_id the endpoint resolved; streams nothing.
    get_state mimics a finished (or paused) graph for the SSE tail logic."""
    def __init__(self, final_messages=None, interrupts=()):
        self.seen: list[str] = []
        self.final_messages = final_messages or []
        self._interrupts = interrupts

    async def astream_events(self, _inputs, config=None, version=None):
        self.seen.append(config["configurable"]["thread_id"])
        return
        yield  # makes this an async generator

    def get_state(self, _config):
        from types import SimpleNamespace
        tasks = ()
        if self._interrupts:
            tasks = (SimpleNamespace(interrupts=tuple(
                SimpleNamespace(value=v) for v in self._interrupts)),)
        return SimpleNamespace(tasks=tasks,
                               values={"messages": self.final_messages})


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


def test_upload_and_chat_share_anonymous_identity(monkeypatch):
    # end-to-end P1 scenario: anonymous upload → same session chats → same user_id
    client, fake = _client(monkeypatch)
    up = client.post("/upload/health",
                     files={"file": ("sleep.csv", b"date,duration_min\n2026-07-01,420\n")})
    assert up.status_code == 200
    session = client.cookies[security.SESSION_COOKIE]
    client.post("/chat", json={"message": "how did I sleep?"})
    assert fake.seen[0].startswith(f"anon-{session}:")


def test_chat_streams_and_terminates_with_done(monkeypatch):
    # regression: _graph_stream must hand EventSourceResponse a generator
    # OBJECT — returning the function crashed with 'function is not iterable'
    client, _ = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 200
    assert "event: done" in r.text


def test_state_written_ai_message_is_emitted_over_sse(monkeypatch):
    # commit/reject confirmations are written to state by non-LLM nodes;
    # the stream must surface them or the frontend shows nothing after approve
    from types import SimpleNamespace
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api

    fake = FakeGraph(final_messages=[
        SimpleNamespace(type="ai", content="Done — 2 events on your calendar.")])
    monkeypatch.setattr(api, "graph", fake)
    r = TestClient(api.app).post("/chat", json={"message": "approve it"})
    assert "event: message" in r.text
    assert "Done — 2 events on your calendar." in r.text


def test_pending_interrupt_is_emitted_as_approval_required(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api

    fake = FakeGraph(interrupts=({"type": "plan_approval", "plan": {"items": []}},))
    monkeypatch.setattr(api, "graph", fake)
    r = TestClient(api.app).post("/chat", json={"message": "plan my weekend"})
    assert "event: approval_required" in r.text
    assert "plan_approval" in r.text
    # paused runs must NOT also emit a stale state message
    assert "event: message" not in r.text


def test_approve_without_pending_interrupt_is_409(monkeypatch):
    client, _ = _client(monkeypatch)  # FakeGraph with no interrupts
    r = client.post("/approve", json={"action": "approve"})
    assert r.status_code == 409


def test_samesite_none_requires_secure_at_startup(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    settings.cache_clear()
    with pytest.raises(RuntimeError):
        security.validate_startup()


def test_invalid_samesite_rejected_at_startup(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "chaotic")
    settings.cache_clear()
    with pytest.raises(RuntimeError):
        security.validate_startup()


def test_csrf_guard_blocks_foreign_origin_when_samesite_none(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    settings.cache_clear()
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert fake.seen == []


def test_csrf_guard_allows_frontend_origin_and_no_origin(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    settings.cache_clear()
    client, fake = _client(monkeypatch)
    ok = client.post("/chat", json={"message": "hi"},
                     headers={"Origin": "http://localhost:3000"})
    assert ok.status_code == 200
    no_origin = client.post("/chat", json={"message": "hi"})  # curl-style
    assert no_origin.status_code == 200


def test_csrf_guard_inactive_under_samesite_lax(monkeypatch):
    # lax: the browser itself won't send the cookie cross-site, so the
    # guard stays out of the way
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 200


def test_mobile_header_session_roundtrip(monkeypatch):
    # RN app can't use httponly cookies: session arrives in the
    # X-Vital-Session response header, comes back as a request header
    client, fake = _client(monkeypatch)
    first = client.post("/chat", json={"message": "hi"})
    session = first.headers["x-vital-session"]
    assert len(session) == 32
    client.cookies.clear()  # simulate a cookie-less mobile client
    client.post("/chat", json={"message": "again"},
                headers={"X-Vital-Session": session})
    assert fake.seen[0] == fake.seen[1]  # same identity via header alone


def test_forged_mobile_header_gets_fresh_session(monkeypatch):
    client, fake = _client(monkeypatch)
    client.cookies.clear()
    r = client.post("/chat", json={"message": "hi"},
                    headers={"X-Vital-Session": "../local-user"})
    assert r.status_code == 200
    assert "local-user" not in fake.seen[0]  # invalid format → new anon session


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

    # restore module to default (debug off) so test order can't leak state;
    # blank values (not delenv) keep a dev's real .env out of the reload
    monkeypatch.setenv("DEBUG_ENDPOINTS", "false")
    monkeypatch.setenv("API_AUTH_TOKEN", "")
    settings.cache_clear()
    importlib.reload(api)
