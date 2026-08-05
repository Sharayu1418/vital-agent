"""Live semantic-memory evaluation. Skipped unless explicitly enabled.

    MEMORY_LIVE_EVAL=1 uv run pytest tests/test_memory_live.py -s

Everything else in the suite uses a content-word overlap embedder. That
proves the MECHANISM — similarity drives dedup, the threshold is honoured,
failures degrade safely — but it cannot judge that "ceramics" means
"pottery" or that "User is in Albany" and "User is located in or near
Albany" are the same fact. Only real embeddings can, so the threshold is
validated here.

Same split as the crisis classifier, and for the same reason: an offline
stand-in that cannot do the hard part must not be trusted to report on it.

The cases below are the REAL duplicates observed in production, copied out
of the "What VITAL knows" panel.
"""
import os

os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pathlib

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MEMORY_LIVE_EVAL") != "1",
    reason="live eval needs Vertex credentials; set MEMORY_LIVE_EVAL=1")


# The four rows that were all one fact.
ALBANY_DUPLICATES = [
    "User is in Albany.",
    "User is located in or near Albany.",
    "User is located in Albany/Guilderland.",
    "User is interested in Albany.",
]

# Pairs that must NOT be merged — the failure mode of an over-tuned
# threshold is eating distinct memories, which is worse than a duplicate.
DISTINCT_PAIRS = [
    ("User has started rock climbing on Tuesdays.", "User wants to go swimming."),
    ("User lives in Albany.", "User dislikes gyms."),
    ("User is a beginner at pottery.", "User is an experienced runner."),
    ("User prefers mornings.", "User has a low budget."),
]


@pytest.fixture(scope="module", autouse=True)
def live_credentials():
    """conftest pins GOOGLE_CLOUD_PROJECT to 'test' and swaps in the offline
    embedder. Undo both, or this file would silently measure the fake — the
    exact trap the first crisis eval fell into."""
    env_file = pathlib.Path(__file__).resolve().parent.parent / ".env"
    values = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")

    project = values.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("MEMORY_LIVE_PROJECT")
    if not project or project == "test":
        pytest.fail(
            "No real GOOGLE_CLOUD_PROJECT. Put it in vital-app/.env or export "
            "MEMORY_LIVE_PROJECT. Refusing to run: otherwise this measures the "
            "offline stand-in and reports it as real embedding quality.")

    os.environ["GOOGLE_CLOUD_PROJECT"] = project
    if values.get("GOOGLE_CLOUD_LOCATION"):
        os.environ["GOOGLE_CLOUD_LOCATION"] = values["GOOGLE_CLOUD_LOCATION"]
    os.environ.pop("EMBEDDING_DIMS", None)

    from vital.config import settings
    settings.cache_clear()
    yield project
    settings.cache_clear()


def _real_store():
    """A store using REAL embeddings, bypassing conftest's stand-in."""
    import importlib

    from langgraph.store.memory import InMemoryStore

    from vital import memory
    importlib.reload(memory)
    return memory, InMemoryStore(index=memory.index_config())


def test_the_albany_duplicates_collapse_to_one():
    """The reported bug: four rows, one fact."""
    memory, store = _real_store()
    for i, fact in enumerate(ALBANY_DUPLICATES):
        memory.remember(store, "live-user", "…", _extractor(memory, fact))

    kept = [m["fact"] for m in memory.all_memories(store, "live-user")]
    print(f"\n  {len(ALBANY_DUPLICATES)} variants -> {len(kept)} stored:")
    for fact in kept:
        print(f"    - {fact}")

    assert len(kept) <= 2, (
        f"threshold {memory.settings().memory_dedup_threshold} left {len(kept)} "
        f"rows for one fact: {kept}")


def test_distinct_facts_are_not_merged():
    """The opposite failure, and the worse one: an over-tuned threshold
    silently eats memories that were never duplicates."""
    memory, store = _real_store()
    for i, (a, b) in enumerate(DISTINCT_PAIRS):
        user = f"pair-{i}"
        memory.remember(store, user, "…", _extractor(memory, a))
        memory.remember(store, user, "…", _extractor(memory, b))
        kept = [m["fact"] for m in memory.all_memories(store, user)]
        assert len(kept) == 2, f"merged two distinct facts: {a!r} + {b!r} -> {kept}"


def test_recall_finds_a_fact_by_meaning_not_words():
    """The retrieval half. Keyword overlap could never do this — the query
    and the fact share no content words."""
    memory, store = _real_store()
    for fact in ["User is into ceramics.",
                 "User dislikes gyms.",
                 "User lives in Albany."]:
        memory.remember(store, "live-user-2", "…", _extractor(memory, fact))

    top = memory.recall(store, "live-user-2", "any pottery classes nearby?", limit=1)
    print(f"\n  'pottery' query -> {top}")
    assert top == ["User is into ceramics."]


def _extractor(memory_mod, fact_text):
    class One:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, _prompt):
            return memory_mod.FactList(
                facts=[memory_mod.Fact(fact=fact_text, confidence=0.9)])

    return One()
