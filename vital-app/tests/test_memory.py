"""Memory tests over a real InMemoryStore with a fake extractor LLM.

The store is built with the SAME index config production uses, so dedup and
recall exercise real vector similarity. conftest supplies a deterministic
offline embedder — a genuine bag-of-words vector, not noise, so similar
sentences really do score higher.

Covers: storing, confidence floor, semantic dedupe-overwrite, semantic
recall, user isolation, forget."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest
from langgraph.store.memory import InMemoryStore

from vital import memory
from vital.config import settings
from vital.memory import Fact, FactList


class FakeExtractor:
    def __init__(self, facts):
        self.facts = facts

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _prompt):
        return FactList(facts=self.facts)


@pytest.fixture
def store():
    # indexed exactly as production is; a bare InMemoryStore has no vectors,
    # so search(query=...) would return unranked results and every dedup and
    # recall assertion below would pass or fail for the wrong reason
    return InMemoryStore(index=memory.index_config())


def test_stores_confident_facts_only(store):
    llm = FakeExtractor([
        Fact(fact="User lives in Brooklyn", confidence=0.95),
        Fact(fact="User might like jazz", confidence=0.3),  # below floor
    ])
    assert memory.remember(store, "u1", "…", llm) == 1
    facts = [m["fact"] for m in memory.all_memories(store, "u1")]
    assert facts == ["User lives in Brooklyn"]


def test_similar_fact_overwrites_instead_of_duplicating(store, monkeypatch):
    """MECHANISM: above the threshold, a new fact replaces the old key.

    The threshold is set explicitly because the offline embedder is a
    content-word proxy, not a semantic model — asserting against the
    production 0.82 here would be testing the fake, not the code. Real
    thresholds are validated in test_memory_live.py.
    """
    monkeypatch.setenv("MEMORY_DEDUP_THRESHOLD", "0.5")
    settings.cache_clear()
    memory.remember(store, "u1", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn", confidence=0.9)]))
    memory.remember(store, "u1", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn NY", confidence=0.95)]))
    mems = memory.all_memories(store, "u1")
    assert len(mems) == 1                      # updated, not appended
    assert mems[0]["fact"] == "User lives in Brooklyn NY"


def test_a_fact_below_the_threshold_is_kept_alongside(store, monkeypatch):
    """The other half of the mechanism: dissimilar facts must coexist, or
    dedup would quietly eat unrelated memories."""
    monkeypatch.setenv("MEMORY_DEDUP_THRESHOLD", "0.99")
    settings.cache_clear()
    memory.remember(store, "u1", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn", confidence=0.9)]))
    memory.remember(store, "u1", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn NY", confidence=0.95)]))
    assert len(memory.all_memories(store, "u1")) == 2


def test_an_embedding_failure_skips_the_write_not_the_turn(store, monkeypatch):
    """Memory must never break a conversation. A half-written fact with no
    vector would be worse: visible in the panel, unreachable by recall."""
    class Broken:
        def search(self, *a, **k):
            raise RuntimeError("embedding service down")

        def put(self, *a, **k):
            raise RuntimeError("embedding service down")

    stored = memory.remember(
        Broken(), "u1", "…",
        FakeExtractor([Fact(fact="User lives in Brooklyn", confidence=0.9)]))
    assert stored == 0


def test_distinct_facts_coexist(store):
    memory.remember(store, "u1", "…", FakeExtractor([
        Fact(fact="User lives in Brooklyn", confidence=0.9),
        Fact(fact="User dislikes gyms", confidence=0.9),
        Fact(fact="User is into ceramics", confidence=0.8),
    ]))
    assert len(memory.all_memories(store, "u1")) == 3


def test_recall_ranks_by_meaning(store):
    memory.remember(store, "u1", "…", FakeExtractor([
        Fact(fact="User dislikes gyms", confidence=0.9),
        Fact(fact="User is into ceramics and pottery", confidence=0.8),
        Fact(fact="User lives in Brooklyn", confidence=0.95),
    ]))
    top = memory.recall(store, "u1", "any pottery classes nearby?", limit=1)
    assert top == ["User is into ceramics and pottery"]


def test_recall_returns_profile_when_no_overlap(store):
    memory.remember(store, "u1", "…", FakeExtractor([
        Fact(fact="User lives in Brooklyn", confidence=0.95),
    ]))
    assert memory.recall(store, "u1", "zzz qqq") == ["User lives in Brooklyn"]


def test_recall_empty_for_unknown_user(store):
    assert memory.recall(store, "stranger", "anything") == []


def test_users_are_isolated(store):
    memory.remember(store, "alice", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn", confidence=0.9)]))
    assert memory.all_memories(store, "bob") == []


def test_forget_deletes(store):
    memory.remember(store, "u1", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn", confidence=0.9)]))
    key = memory.all_memories(store, "u1")[0]["key"]
    memory.forget(store, "u1", key)
    assert memory.all_memories(store, "u1") == []
