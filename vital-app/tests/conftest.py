"""Global test fixtures.

Every test gets an isolated SQLite + data dir under tmp_path — tests must
NEVER touch the real vital.db or data/ (Phase 4 review finding: /chat's
budget check made even API tests hit the database)."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

# A developer's real .env must never leak into tests. pydantic-settings
# falls back to the env FILE only when a variable is absent from the
# environment — so pin these HERE, at import time, before any module-level
# settings() call (api.py registers /debug routes at import).
os.environ["API_AUTH_TOKEN"] = ""
os.environ["DEBUG_ENDPOINTS"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ["TICKETMASTER_API_KEY"] = ""
os.environ["AUTH_REQUIRED"] = "false"  # anonymous access in tests by default

import pytest


@pytest.fixture(autouse=True)
def offline_embeddings(monkeypatch):
    """Memory is semantic now (pgvector via LangGraph's store index), which
    means an embedding call on every write and recall. Tests stay
    zero-network, so patch the single seam — same idea as
    security._firebase_verify and the crisis classifier.

    The stand-in is a content-word overlap vector — a rough proxy, NOT a
    semantic model. It cannot judge that "ceramics" means "pottery", so
    offline tests here assert the MECHANISM (similarity drives dedup,
    threshold is respected, failures degrade safely) and set explicit
    thresholds to do it. Real semantic quality is measured against live
    embeddings in test_memory_live.py, the same split used for the crisis
    classifier.
    """
    import hashlib
    import re

    from vital import memory
    from vital.config import settings

    FAKE_DIMS = 16
    STOPWORDS = {"user", "is", "in", "the", "a", "an", "and", "of", "to",
                 "any", "near", "nearby", "for"}

    def _vec(text: str) -> list[float]:
        v = [0.0] * FAKE_DIMS
        words = re.findall(r"[a-z0-9]+", text.lower())
        for word in words:
            if word in STOPWORDS:
                continue      # or every fact matches every other one
            idx = int(hashlib.md5(word.encode()).hexdigest(), 16) % FAKE_DIMS
            v[idx] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def fake_embeddings():
        # A plain callable, not an object with embed_documents/embed_query.
        # LangGraph only recognises the method interface on a real
        # Embeddings subclass; anything else it treats as an
        # EmbeddingsFunc and calls directly.
        return lambda texts: [_vec(t) for t in texts]

    # the index is built with cfg.embedding_dims, so the fake's width has to
    # match or every put() fails on a dimension mismatch
    monkeypatch.setenv("EMBEDDING_DIMS", str(FAKE_DIMS))
    settings.cache_clear()
    monkeypatch.setattr(memory, "_embeddings", fake_embeddings)
    memory.get_store.cache_clear()
    yield
    memory.get_store.cache_clear()
    settings.cache_clear()


@pytest.fixture(autouse=True)
def offline_crisis_classifier(monkeypatch):
    """The crisis screen calls a model (P0-4). Tests stay zero-network, so
    patch the single seam — same idea as security._firebase_verify.

    The stand-in is the DETERMINISTIC matcher, not a stub that always says
    'clear': that way every existing crisis test still exercises real
    detection logic, and a test that needs specific classifier behaviour
    passes its own `classifier=` instead.

    Skipped under CRISIS_LIVE_EVAL=1, which exists precisely to exercise the
    real model. Reloading the module to undo this patch was too fragile —
    other modules keep a reference to the old one.
    """
    if os.environ.get("CRISIS_LIVE_EVAL") == "1":
        return

    from vital import guardrails

    def offline(context: str) -> str:
        return "crisis" if guardrails.deterministic_crisis(context) else "clear"

    monkeypatch.setattr(guardrails, "_default_classifier", offline)


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    from vital.config import settings
    settings.cache_clear()
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    # pydantic-settings also reads the repo's real .env — a developer's
    # API_AUTH_TOKEN / DEBUG_ENDPOINTS / DATABASE_URL there must never
    # change test behavior. Env vars override env_file, so pin safe values.
    monkeypatch.setenv("API_AUTH_TOKEN", "")       # blank = not configured
    monkeypatch.setenv("DEBUG_ENDPOINTS", "false")
    monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "lax")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:3000")
    yield
    settings.cache_clear()
