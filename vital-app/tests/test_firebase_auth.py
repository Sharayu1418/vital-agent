"""Firebase Google Sign-In: identity linking, isolation, and hard-401s.

No network, no real Firebase: tests monkeypatch security._firebase_verify
(the single seam around firebase_admin.auth.verify_id_token). Everything
else — routes, cookies, resolve_identity, auth_identities — is real.
"""
import importlib
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import security, storage
from vital.config import settings

# distinct exception types so the "no detail leak" test is meaningful
class _Expired(Exception): ...
class _WrongProject(Exception): ...


TOKENS = {
    "tok-alice": {"uid": "fb-alice"},
    "tok-bob": {"uid": "fb-bob"},
}


def _fake_verify(token: str) -> dict:
    if token == "tok-expired":
        raise _Expired("Token expired at 12:00 UTC")
    if token == "tok-wrong-project":
        raise _WrongProject("aud claim was other-project, expected vital")
    if token in TOKENS:
        return TOKENS[token]
    raise ValueError("could not verify signature")


@pytest.fixture(autouse=True)
def firebase_enabled(monkeypatch):
    monkeypatch.setenv("FIREBASE_AUTH_ENABLED", "true")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "vital-test")
    monkeypatch.setattr(security, "_firebase_verify", _fake_verify)
    settings.cache_clear()
    yield
    settings.cache_clear()


def _client(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    from tests.test_api_security import FakeGraph
    import vital.api as api

    fake = FakeGraph()
    monkeypatch.setattr(api, "graph", fake)
    return TestClient(api.app), fake


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- resolution behavior ----------

def test_no_bearer_keeps_anonymous_behavior(monkeypatch):
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi", "thread_id": "t1"})
    assert r.status_code == 200
    assert fake.seen[0].startswith("anon-")
    assert security.SESSION_COOKIE in r.cookies


def test_firebase_token_resolves_to_stable_internal_identity(monkeypatch):
    client, fake = _client(monkeypatch)
    client.post("/chat", json={"message": "hi", "thread_id": "t1"},
                headers=_bearer("tok-alice"))
    client.post("/chat", json={"message": "again", "thread_id": "t2"},
                headers=_bearer("tok-alice"))
    u1 = fake.seen[0].rsplit(":", 1)[0]
    u2 = fake.seen[1].rsplit(":", 1)[0]
    assert u1 == u2                        # stable across requests
    assert "fb-alice" not in u1            # Firebase UID never IS the user_id namespace leak


def test_same_uid_on_two_clients_shares_identity(monkeypatch):
    # "two devices": separate TestClients = separate cookie jars
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    from tests.test_api_security import FakeGraph
    import vital.api as api

    fake = FakeGraph()
    monkeypatch.setattr(api, "graph", fake)
    a, b = TestClient(api.app), TestClient(api.app)
    a.post("/chat", json={"message": "hi", "thread_id": "t1"}, headers=_bearer("tok-alice"))
    b.post("/chat", json={"message": "hi", "thread_id": "t1"}, headers=_bearer("tok-alice"))
    assert fake.seen[0] == fake.seen[1]


def test_different_uids_stay_isolated(monkeypatch):
    client, fake = _client(monkeypatch)
    client.post("/chat", json={"message": "hi", "thread_id": "t1"}, headers=_bearer("tok-alice"))
    client.post("/chat", json={"message": "hi", "thread_id": "t1"}, headers=_bearer("tok-bob"))
    assert fake.seen[0] != fake.seen[1]


def test_first_sign_in_links_current_anonymous_identity(monkeypatch):
    client, fake = _client(monkeypatch)
    client.post("/chat", json={"message": "anon hello", "thread_id": "t1"})
    anon_user = fake.seen[0].rsplit(":", 1)[0]
    # same browser signs in: cookie rides along with the first token request
    client.post("/chat", json={"message": "signed in", "thread_id": "t1"},
                headers=_bearer("tok-alice"))
    assert fake.seen[1].rsplit(":", 1)[0] == anon_user  # data followed the account


def test_client_cannot_choose_linked_identity(monkeypatch):
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi", "thread_id": "t1",
                                   "user_id": "victim-user"},
                    headers=_bearer("tok-alice"))
    assert r.status_code == 200
    assert not fake.seen[0].startswith("victim-user")


def test_signed_out_session_cannot_access_linked_data(monkeypatch):
    client, fake = _client(monkeypatch)
    client.post("/chat", json={"message": "anon", "thread_id": "t1"})
    linked_user = fake.seen[0].rsplit(":", 1)[0]
    client.post("/chat", json={"message": "link me", "thread_id": "t1"},
                headers=_bearer("tok-alice"))
    assert fake.seen[1].rsplit(":", 1)[0] == linked_user
    # sign out client-side only: same old cookie, no token
    r = client.post("/chat", json={"message": "who am I now?", "thread_id": "t1"})
    assert r.status_code == 200
    assert fake.seen[2].rsplit(":", 1)[0] != linked_user  # rejected + rotated
    assert security.SESSION_COOKIE in r.cookies            # fresh session issued
    # but the account itself still reaches its data
    client.post("/chat", json={"message": "back", "thread_id": "t1"},
                headers=_bearer("tok-alice"))
    assert fake.seen[3].rsplit(":", 1)[0] == linked_user


def test_upload_then_sign_in_then_sleep_recent_follows_account(monkeypatch):
    # end-to-end across routes: anonymous upload → sign in → data visible
    # via token from a fresh client (different "device"), NOT via old cookie
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api
    from tests.test_api_security import FakeGraph

    monkeypatch.setattr(api, "graph", FakeGraph())
    device1 = TestClient(api.app)
    up = device1.post("/upload/health",
                      files={"file": ("s.csv", b"date,duration_min\n2026-07-01,420\n")})
    assert up.status_code == 200
    device1.get("/memories", headers=_bearer("tok-alice"))  # links the anon id

    device2 = TestClient(api.app)  # no cookie at all
    r = device2.get("/sleep/recent", headers=_bearer("tok-alice"))
    assert any(n["duration_min"] == 420 for n in r.json()["nights"])

    # old cookie alone no longer sees it
    r2 = device1.get("/sleep/recent")
    assert all(n["duration_min"] != 420 for n in r2.json()["nights"])


# ---------- hard 401s ----------

@pytest.mark.parametrize("token", ["tok-garbage", "tok-expired", "tok-wrong-project"])
def test_bad_tokens_are_401_and_never_anonymous(monkeypatch, token):
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi"}, headers=_bearer(token))
    assert r.status_code == 401
    assert fake.seen == []                       # no fallback identity work
    assert security.SESSION_COOKIE not in r.cookies


@pytest.mark.parametrize("token", ["tok-expired", "tok-wrong-project", "tok-garbage"])
def test_401_detail_does_not_leak_verification_info(monkeypatch, token):
    client, _ = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi"}, headers=_bearer(token))
    detail = r.json()["detail"].lower()
    for fragment in ("expired", "project", "aud", "signature", "12:00"):
        assert fragment not in detail


def test_firebase_token_cannot_reach_debug_routes(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api

    monkeypatch.setenv("DEBUG_ENDPOINTS", "true")
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    reloaded = importlib.reload(api)
    try:
        client = TestClient(reloaded.app)
        r = client.get("/debug/state/u/t", headers=_bearer("tok-alice"))
        assert r.status_code == 401              # authenticated ≠ trusted
        assert client.get("/debug/state/u/t").status_code == 401
    finally:
        monkeypatch.setenv("DEBUG_ENDPOINTS", "false")
        monkeypatch.setenv("API_AUTH_TOKEN", "")
        settings.cache_clear()
        importlib.reload(api)


def test_internal_token_still_asserts_user_id(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi", "user_id": "alice", "thread_id": "t1"},
                    headers=_bearer("s3cret"))
    assert r.status_code == 200
    assert fake.seen[0] == "alice:t1"            # unchanged internal behavior


# ---------- mapping + threads + logout ----------

def test_mapping_is_conflict_safe():
    first = storage.resolve_external_identity("firebase", "fb-race", "anon-" + "c" * 32)
    second = storage.resolve_external_identity("firebase", "fb-race", "anon-" + "d" * 32)
    assert first == second == "anon-" + "c" * 32  # second candidate ignored


def test_claimed_anon_identity_cannot_be_claimed_twice():
    anon = "anon-" + "e" * 32
    a = storage.resolve_external_identity("firebase", "fb-one", anon)
    b = storage.resolve_external_identity("firebase", "fb-two", anon)
    assert a == anon
    assert b != anon and b.startswith("usr-")     # minted fresh, no merge


def test_threads_index_per_account(monkeypatch):
    client, fake = _client(monkeypatch)
    client.post("/chat", json={"message": "plan my river swim", "thread_id": "th1"},
                headers=_bearer("tok-alice"))
    mine = client.get("/threads", headers=_bearer("tok-alice")).json()["threads"]
    assert [t["thread_id"] for t in mine] == ["th1"]
    assert mine[0]["title"].startswith("plan my river swim")
    assert "user_id" not in mine[0]               # internal ids never exposed
    others = client.get("/threads", headers=_bearer("tok-bob")).json()["threads"]
    assert others == []
    # anonymous chats don't write the index
    client.post("/chat", json={"message": "anon msg", "thread_id": "th9"})
    assert client.get("/threads", headers=_bearer("tok-bob")).json()["threads"] == []


def test_thread_delete_only_unlists_the_callers_row(monkeypatch):
    client, fake = _client(monkeypatch)
    client.post("/chat", json={"message": "alice's plan", "thread_id": "tdel"},
                headers=_bearer("tok-alice"))
    # bob deleting the same thread id touches only BOB's (empty) index
    r = client.delete("/threads/tdel", headers=_bearer("tok-bob"))
    assert r.status_code == 200
    alice = client.get("/threads", headers=_bearer("tok-alice")).json()["threads"]
    assert [t["thread_id"] for t in alice] == ["tdel"]   # untouched by bob
    # alice unlists her own row
    assert client.delete("/threads/tdel",
                         headers=_bearer("tok-alice")).status_code == 200
    assert client.get("/threads", headers=_bearer("tok-alice")).json()["threads"] == []
    # unlisting is NOT erasure: chatting on the id re-lists it
    client.post("/chat", json={"message": "back again", "thread_id": "tdel"},
                headers=_bearer("tok-alice"))
    alice = client.get("/threads", headers=_bearer("tok-alice")).json()["threads"]
    assert [t["thread_id"] for t in alice] == ["tdel"]


def test_thread_delete_rejects_invalid_ids(monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.delete("/threads/..%2Fetc", headers=_bearer("tok-alice"))
    assert r.status_code in (404, 422)   # path-safe either way


# ---------- AUTH_REQUIRED=true (OAuth-first production mode) ----------

@pytest.fixture
def auth_required(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    settings.cache_clear()
    yield
    settings.cache_clear()


def test_require_auth_rejects_anonymous_user_data_routes(monkeypatch, auth_required):
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 401
    assert fake.seen == []                          # no identity was minted
    assert security.SESSION_COOKIE not in r.cookies  # and no session either
    for path in ("/sleep/recent", "/calendar", "/memories",
                 "/activity-posts", "/threads", "/threads/t1/messages"):
        assert client.get(path).status_code == 401, path
    assert client.post("/feedback", json={"rating": "up"}).status_code == 401
    assert client.post("/upload/health",
                       files={"file": ("s.csv", b"date,duration_min\n2026-07-01,420\n")}
                       ).status_code == 401
    assert client.delete("/threads/t1").status_code == 401


def test_require_auth_keeps_public_routes_public(monkeypatch, auth_required):
    client, _ = _client(monkeypatch)
    assert client.get("/healthz").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.post("/auth/logout").status_code == 200  # sign-out must work


def test_require_auth_firebase_token_still_works(monkeypatch, auth_required):
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi", "thread_id": "t1"},
                    headers=_bearer("tok-alice"))
    assert r.status_code == 200
    assert len(fake.seen) == 1


def test_require_auth_internal_token_still_works(monkeypatch, auth_required):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    settings.cache_clear()
    client, fake = _client(monkeypatch)
    r = client.post("/chat", json={"message": "hi", "user_id": "ops", "thread_id": "t1"},
                    headers=_bearer("s3cret"))
    assert r.status_code == 200
    assert fake.seen[0] == "ops:t1"


def test_firebase_app_adopts_existing_default_app(monkeypatch):
    # a cache_clear() (tests / startup re-validation) must not re-init and
    # raise 'default app already exists' — get_app() is tried first
    inits = []

    class _FakeApp: ...

    fake = _FakeApp()
    state = {"app": None}

    def get_app():
        if state["app"] is None:
            raise ValueError("no default app")
        return state["app"]

    def initialize_app(*_a, **_k):
        inits.append(1)
        state["app"] = fake
        return fake

    fake_admin = type("m", (), {"get_app": staticmethod(get_app),
                                "initialize_app": staticmethod(initialize_app)})
    fake_creds = type("c", (), {"ApplicationDefault": staticmethod(lambda: None)})
    monkeypatch.setitem(__import__("sys").modules, "firebase_admin", fake_admin)
    monkeypatch.setitem(__import__("sys").modules, "firebase_admin.credentials",
                        fake_creds)
    monkeypatch.setattr(fake_admin, "credentials", fake_creds, raising=False)

    security._firebase_app.cache_clear()
    first = security._firebase_app()
    security._firebase_app.cache_clear()   # simulate reload / re-validation
    second = security._firebase_app()
    assert first is second is fake
    assert sum(inits) == 1                 # initialized once, adopted after
    security._firebase_app.cache_clear()


def test_startup_refuses_auth_required_without_authenticator(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("FIREBASE_AUTH_ENABLED", "false")
    monkeypatch.setenv("API_AUTH_TOKEN", "")
    settings.cache_clear()
    with pytest.raises(RuntimeError, match="AUTH_REQUIRED"):
        security.validate_startup()


def test_logout_expires_cookie_and_keeps_data(monkeypatch):
    client, fake = _client(monkeypatch)
    client.post("/chat", json={"message": "anon", "thread_id": "t1"})
    client.post("/chat", json={"message": "link", "thread_id": "t1"},
                headers=_bearer("tok-alice"))
    linked_user = fake.seen[1].rsplit(":", 1)[0]

    r = client.post("/auth/logout")
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert security.SESSION_COOKIE in set_cookie and "Max-Age=0" in set_cookie

    # signing back in restores the same identity → data not deleted
    client.post("/chat", json={"message": "returned", "thread_id": "t1"},
                headers=_bearer("tok-alice"))
    assert fake.seen[-1].rsplit(":", 1)[0] == linked_user
