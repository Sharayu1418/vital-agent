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


def test_dedup_does_not_chain(monkeypatch):
    """Three facts, laid out so the ends are nowhere near each other.

    THE BUG THIS EXISTS FOR
    -----------------------
    A merge overwrites the row it matched. So whatever landed last is what
    the next fact gets compared against, and a cluster can walk: A absorbs
    B, then C is judged against B, absorbs the row, and A and C end up
    merged having never once been compared. Single-linkage clustering, and
    single linkage chains — this is its textbook failure.

    In the retrieval eval it turned eighteen distinct facts into ten, and it
    destroyed the absorbed text on the way, which is why reconstructing the
    merges afterwards produced impossible sub-threshold numbers.

    Raising the threshold is not a fix and this test is built to show why:
    the endpoints below are 0.697 apart and the threshold is 0.87. A higher
    bar only demands a longer chain.

    The embedder is three points on a unit circle rather than the shared
    offline fake, because the whole test is about exact distances and a
    bag-of-words proxy cannot place them.
    """
    import math

    angle = 0.40                      # neighbours ~0.92, ends ~0.70
    positions = {"A": 0.0, "B": angle, "C": 2 * angle}

    def embed(texts):
        out = []
        for text in texts:
            radians = next((r for name, r in positions.items()
                            if text.startswith(name)), 0.0)
            out.append([math.cos(radians), math.sin(radians)])
        return out

    monkeypatch.setenv("EMBEDDING_DIMS", "2")
    monkeypatch.setenv("MEMORY_DEDUP_THRESHOLD", "0.87")
    settings.cache_clear()
    monkeypatch.setattr(memory, "_embeddings", lambda: embed)
    memory.get_store.cache_clear()

    # State the geometry, so a future reader can see the endpoints really
    # are far apart and the test is not passing by accident.
    assert memory.similarity("A fact", "B fact", via=embed) > 0.87
    assert memory.similarity("B fact", "C fact", via=embed) > 0.87
    assert memory.similarity("A fact", "C fact", via=embed) < 0.87

    chained = InMemoryStore(index=memory.index_config())
    for name in ("A", "B", "C"):
        memory.remember(chained, "u1", "…",
                        FakeExtractor([Fact(fact=f"{name} fact",
                                            confidence=0.9)]))

    facts = sorted(m["fact"] for m in memory.all_memories(chained, "u1"))
    assert facts == ["B fact", "C fact"], (
        f"expected C to start its own row, got {facts} — a fact merged into "
        "a row it was never compared against")


def test_a_merge_keeps_the_row_anchored_to_its_original_text(store, monkeypatch):
    """What stops chaining coming back one write later.

    The anchor is only useful if a merge leaves it alone. If the write
    re-anchored the row to its newest text, every row would re-anchor on
    every merge and the comparison would be back to single linkage.
    """
    monkeypatch.setenv("MEMORY_DEDUP_THRESHOLD", "0.5")
    settings.cache_clear()
    memory.remember(store, "u1", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn",
                                        confidence=0.9)]))
    memory.remember(store, "u1", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn NY",
                                        confidence=0.95)]))
    row = memory.all_memories(store, "u1")[0]
    assert row["fact"] == "User lives in Brooklyn NY"      # current text moves
    assert row["anchor"] == "User lives in Brooklyn"       # anchor does not


def test_a_row_written_before_anchors_existed_still_works(store, monkeypatch):
    """Production rows predate this field. They must not fail every
    comparison and fragment into duplicates — falling back to the current
    fact makes an un-anchored row behave exactly as it did before, and it
    picks up a real anchor the next time it is written."""
    monkeypatch.setenv("MEMORY_DEDUP_THRESHOLD", "0.5")
    settings.cache_clear()
    store.put(memory._ns("u1"), "legacy",
              {"fact": "User lives in Brooklyn", "confidence": 0.9})

    assert memory.anchor_of({"fact": "User lives in Brooklyn"}) == \
        "User lives in Brooklyn"

    memory.remember(store, "u1", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn NY",
                                        confidence=0.95)]))
    mems = memory.all_memories(store, "u1")
    assert len(mems) == 1                      # matched the legacy row
    assert mems[0]["anchor"] == "User lives in Brooklyn"


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
