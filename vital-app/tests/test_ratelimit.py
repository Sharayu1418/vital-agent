"""Request rate limiting.

The per-user daily TOKEN budget capped model spend. Nothing capped
requests — so a loop against /chat was a bill rather than an error, and
endpoints that never touch a model had no ceiling at all.

Two properties matter and both fail quietly: the limit must actually bite,
and it must not be resettable by the client.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import ratelimit


@pytest.fixture(autouse=True)
def clean():
    """Rate limits are global state. Without this, one test's traffic makes
    the next one fail for reasons unrelated to its subject."""
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_it_allows_normal_use():
    limit, _ = ratelimit.LIMITS["chat"]
    for _ in range(limit):
        allowed, _ = ratelimit.check("chat", "user-1")
        assert allowed


def test_it_blocks_past_the_limit():
    limit, _ = ratelimit.LIMITS["chat"]
    for _ in range(limit):
        ratelimit.check("chat", "user-1")
    allowed, retry_after = ratelimit.check("chat", "user-1")
    assert not allowed
    assert retry_after > 0, "a 429 without Retry-After tells the client nothing"


def test_one_user_cannot_lock_out_another():
    """Keyed per identity. A shared bucket would mean one loop takes the
    whole app down for everybody — a worse outage than the one it prevents."""
    limit, _ = ratelimit.LIMITS["chat"]
    for _ in range(limit + 5):
        ratelimit.check("noisy", "noisy-user")
    allowed, _ = ratelimit.check("chat", "quiet-user")
    assert allowed


def test_buckets_are_independent():
    """Reading the panel should never eat your ability to send a message."""
    limit, _ = ratelimit.LIMITS["read"]
    for _ in range(limit):
        ratelimit.check("read", "user-1")
    allowed, _ = ratelimit.check("chat", "user-1")
    assert allowed


def test_the_window_slides():
    limit, window = ratelimit.LIMITS["chat"]
    now = 1000.0
    for _ in range(limit):
        ratelimit.check("chat", "user-1", now=now)
    assert not ratelimit.check("chat", "user-1", now=now)[0]
    # Past the window, the old hits expire.
    assert ratelimit.check("chat", "user-1", now=now + window + 1)[0]


def test_an_unknown_bucket_is_still_limited():
    """A typo in a bucket name must not silently mean 'unlimited'."""
    allowed, _ = ratelimit.check("not-a-real-bucket", "user-1")
    assert allowed          # first call fine
    limit, _ = ratelimit.LIMITS["read"]
    for _ in range(limit):
        ratelimit.check("not-a-real-bucket", "user-1")
    assert not ratelimit.check("not-a-real-bucket", "user-1")[0]


def test_it_does_not_grow_without_bound():
    """A limiter that leaks memory is a slower version of the problem it
    was added to fix."""
    _, window = ratelimit.LIMITS["read"]
    for i in range(ratelimit._MAX_KEYS + 200):
        ratelimit.check("read", f"user-{i}", now=1000.0)
    # Everything above is stale by now, so the next call should evict.
    ratelimit.check("read", "fresh", now=1000.0 + window * 3)
    assert len(ratelimit._hits) < ratelimit._MAX_KEYS


# ---------- wired into the app ----------

def test_chat_is_rate_limited(monkeypatch):
    """End to end: the 20th request through /chat is fine, the 40th is a
    429. A limiter that exists but is not attached protects nothing."""
    pytest.importorskip("langchain_google_vertexai")
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    import vital.api as api

    class QuietGraph:
        async def astream_events(self, _inputs, config=None, version=None):
            return
            yield        # pragma: no cover — makes this an async generator

        def get_state(self, _config):
            return SimpleNamespace(tasks=(), values={"messages": [],
                                                     "routing_history": []})

    monkeypatch.setattr(api, "graph", QuietGraph())
    client = TestClient(api.app)

    limit, _ = ratelimit.LIMITS["chat"]
    statuses = [client.post("/chat", json={"message": "hi"}).status_code
                for _ in range(limit + 3)]
    assert 429 in statuses, "chat has no ceiling"
    assert statuses[0] == 200, "the very first request should not be limited"


def test_the_limit_key_is_not_client_controlled():
    """Keyed on the RESOLVED identity, after auth. If it keyed on anything
    the client sends, resetting the counter would be a header change."""
    import inspect

    source = inspect.getsource(__import__("vital.api", fromlist=["api"]).Identity.limit)
    assert "self.resolve" in source
    assert "req_user_id" not in source.split("ratelimit.check")[1][:80]
