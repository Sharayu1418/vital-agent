"""Memory tests over a real InMemoryStore with a fake extractor LLM.
Covers: storing, confidence floor, dedupe-overwrite, recall ranking,
user isolation, forget."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest
from langgraph.store.memory import InMemoryStore

from vital import memory
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
    return InMemoryStore()


def test_stores_confident_facts_only(store):
    llm = FakeExtractor([
        Fact(fact="User lives in Brooklyn", confidence=0.95),
        Fact(fact="User might like jazz", confidence=0.3),  # below floor
    ])
    assert memory.remember(store, "u1", "…", llm) == 1
    facts = [m["fact"] for m in memory.all_memories(store, "u1")]
    assert facts == ["User lives in Brooklyn"]


def test_near_duplicate_overwrites_instead_of_duplicating(store):
    memory.remember(store, "u1", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn", confidence=0.9)]))
    memory.remember(store, "u1", "…",
                    FakeExtractor([Fact(fact="User lives in Brooklyn, NY", confidence=0.95)]))
    mems = memory.all_memories(store, "u1")
    assert len(mems) == 1                      # updated, not appended
    assert mems[0]["fact"] == "User lives in Brooklyn, NY"


def test_distinct_facts_coexist(store):
    memory.remember(store, "u1", "…", FakeExtractor([
        Fact(fact="User lives in Brooklyn", confidence=0.9),
        Fact(fact="User dislikes gyms", confidence=0.9),
        Fact(fact="User is into ceramics", confidence=0.8),
    ]))
    assert len(memory.all_memories(store, "u1")) == 3


def test_recall_ranks_by_keyword_overlap(store):
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
