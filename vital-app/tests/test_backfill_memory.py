"""Dedup's scoring, and the backfill script that predicts it.

Both are here because they are the same property: the merge decision must
depend on the embedding model and nothing else. It used to depend on which
store backend was underneath, and that is what broke production.

    InMemoryStore (every test, the live eval)  query -> embed_query
    PostgresStore 3.1.0 (production)           query -> embed_documents

text-embedding-004 is task-typed, so the same pair scores ~0.24 apart
between the two. A threshold tuned against the tests merged every distinct
fact into a single row in production, and no test could have caught it,
because the tests were the thing being agreed with.

So duplicate_key now uses the store to RANK candidates and re-scores them
itself. The tests below pin that: a store can report any score it likes and
the outcome must not move.
"""
import importlib.util
import os
import pathlib
from functools import lru_cache

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest
from langgraph.store.memory import InMemoryStore

from vital import memory

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
          / "scripts" / "backfill_memory_vectors.py")


# ---------- the root cause: the backend must not get a vote ----------

class Hit:
    def __init__(self, key, fact, score):
        self.key = key
        self.value = {"fact": fact}
        self.score = score


class LyingStore:
    """A store whose reported score is deliberately wrong.

    Not a strawman: this is the difference between the two real backends,
    exaggerated to a value no correct implementation could produce.
    """

    def __init__(self, hits, embeddings):
        self._hits = hits
        self.embeddings = embeddings

    def search(self, _ns, query=None, limit=None):
        return self._hits


@pytest.fixture
def embedder():
    return InMemoryStore(index=memory.index_config()).embeddings


def test_a_confident_backend_cannot_force_a_merge(embedder):
    """PostgresStore scored unrelated facts around 0.80 where the tests saw
    0.56. Whatever the store claims, two facts that are not alike must not
    merge."""
    store = LyingStore(
        [Hit("k1", "User is an experienced runner.", score=0.999)], embedder)

    assert memory.duplicate_key(store, "u1", "User has a low budget.") is None, (
        "the store's score decided the merge — that is the production bug")


def test_a_pessimistic_backend_cannot_block_a_merge(embedder):
    """The mirror case. A backend reporting 0.0 for an identical fact must
    not be able to leave a duplicate behind."""
    store = LyingStore([Hit("k1", "User lives in Albany.", score=0.0)], embedder)

    assert memory.duplicate_key(store, "u1", "User lives in Albany.") == "k1"


def test_similarity_is_symmetric(embedder):
    """The property that makes one threshold mean one thing. If the two
    directions disagree, the number depends on argument order, and dedup
    would merge or not depending on which fact arrived first."""
    a, b = "User lives in Albany.", "User is located near Albany."
    assert (memory.similarity(a, b, via=embedder)
            == pytest.approx(memory.similarity(b, a, via=embedder)))


def test_candidates_beyond_the_top_hit_are_considered(embedder):
    """duplicate_key only saw hits[0] before. The store ranks on its own
    scale, so the true duplicate is not reliably first — taking only the top
    hit would let a duplicate through whenever the backend ordered
    differently than we score."""
    store = LyingStore([
        Hit("k1", "User dislikes gyms.", score=0.99),
        Hit("k2", "User lives in Albany.", score=0.01),
    ], embedder)

    assert memory.duplicate_key(store, "u1", "User lives in Albany.") == "k2"


def test_no_candidates_means_no_merge(embedder):
    assert memory.duplicate_key(LyingStore([], embedder), "u1", "anything") is None


def test_a_candidate_with_no_fact_text_is_skipped(embedder):
    """Defensive: a malformed row must not crash the write path, because a
    failed dedup takes the whole memory write with it."""
    store = LyingStore([Hit("k1", "", score=0.99)], embedder)
    assert memory.duplicate_key(store, "u1", "User lives in Albany.") is None


# ---------- the backfill script ----------

@pytest.fixture
def backfill():
    spec = importlib.util.spec_from_file_location("backfill_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_scores_through_the_app(backfill, monkeypatch):
    """It must not have an opinion of its own. It used to, and that is how a
    bulk delete ended up calibrated against a scale the app never used."""
    seen = {}

    def fake_similarity(a, b, via=None):
        seen["args"] = (a, b)
        return 0.5

    monkeypatch.setattr(backfill.memory, "similarity", fake_similarity)

    assert backfill._similarity("store", "keeper fact", "candidate fact") == 0.5
    assert seen["args"] == ("keeper fact", "candidate fact")


def test_the_dry_run_writes_nothing(backfill, monkeypatch):
    """`--apply` is the only mode allowed to touch data. If the dry run ever
    starts writing, the operator's one chance to review a destructive merge
    disappears."""
    written = []

    class Store:
        def list_namespaces(self, prefix=None):
            return [("u1", backfill.memory.NAMESPACE_SUFFIX)]

        def put(self, *a, **kw):
            written.append(a)

        def delete(self, *a, **kw):
            written.append(a)

    # lru_cache, not a bare lambda: the autouse offline_embeddings fixture
    # calls memory.get_store.cache_clear() when it tears down, and fixture
    # finalizers run BEFORE monkeypatch undoes this — so a stand-in without
    # cache_clear blows up in teardown, after the test itself has passed.
    @lru_cache
    def fake_get_store():
        return Store()

    monkeypatch.setattr(backfill.memory, "get_store", fake_get_store)
    monkeypatch.setattr(backfill.memory, "all_memories", lambda store, user: [
        {"key": "k1", "fact": "User is in Albany.", "confidence": 0.9},
        {"key": "k2", "fact": "User is near Albany.", "confidence": 0.8},
    ])
    monkeypatch.setattr(backfill, "_similarity", lambda store, a, b: 0.99)
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/stub")
    backfill.settings.cache_clear()
    monkeypatch.setattr("sys.argv", ["backfill_memory_vectors.py"])

    assert backfill.main() == 0
    assert written == [], f"dry run wrote to the store: {written}"
    backfill.settings.cache_clear()
