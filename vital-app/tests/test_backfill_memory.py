"""The backfill script's merge decision.

This script is the only code in the repo that DELETES memories in bulk, and
it decides what to delete by comparing embeddings — so it has to compare
them on the same scale the threshold was calibrated for.

It did not. `_similarity` embedded both facts as documents while the app's
dedup embeds the incoming fact as a query, and `text-embedding-004` is
task-typed: the same pair scores ~0.24 higher document-to-document. Run at
MEMORY_DEDUP_THRESHOLD=0.63 against document-scale scores, the script would
have merged every distinct fact in the store.

That is the third time the same mistake appeared in this codebase — a
harness measuring one thing while production measures another — so it gets
a test rather than a comment.
"""
import importlib.util
import os
import pathlib

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
          / "scripts" / "backfill_memory_vectors.py")


@pytest.fixture
def backfill():
    spec = importlib.util.spec_from_file_location("backfill_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._embedder.cache_clear()
    yield module
    module._embedder.cache_clear()


class TaskTypedEmbedder:
    """Stands in for a task-typed model.

    Documents and queries land in different subspaces, so a pair scores high
    doc-to-doc and low doc-to-query — the real behaviour, exaggerated so the
    difference is unmistakable. Also records which method saw which text.
    """

    def __init__(self):
        self.documents: list[str] = []
        self.queries: list[str] = []

    def embed_documents(self, texts):
        self.documents.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        self.queries.append(text)
        return [0.0, 1.0]


def test_the_keeper_is_a_document_and_the_candidate_is_a_query(backfill,
                                                               monkeypatch):
    """The whole bug in one assertion. Reversing these, or embedding both
    the same way, puts the score on a scale the threshold does not match."""
    embedder = TaskTypedEmbedder()
    monkeypatch.setattr(backfill.memory, "index_config",
                        lambda: {"embed": embedder})

    backfill._similarity(None, "User is in Albany.", "User is near Albany.")

    assert embedder.documents == ["User is in Albany."], (
        "the SURVIVING fact is the stored one — it must be embedded as a "
        "document, exactly as store.put() embedded it")
    assert embedder.queries == ["User is near Albany."], (
        "the candidate is what dedup passes to store.search(query=...) — "
        "embedding it as a document inflates the score onto a scale "
        "MEMORY_DEDUP_THRESHOLD was never calibrated against")


def test_it_scores_on_the_query_path_not_the_document_path(backfill,
                                                           monkeypatch):
    """Behavioural counterpart to the assertion above: with an embedder
    whose two subspaces are orthogonal, a doc-doc implementation returns
    1.0 and the correct one returns 0.0. A mutation that flips this cannot
    survive both tests."""
    embedder = TaskTypedEmbedder()
    monkeypatch.setattr(backfill.memory, "index_config",
                        lambda: {"embed": embedder})

    score = backfill._similarity(None, "User is in Albany.",
                                 "User is near Albany.")

    assert score == pytest.approx(0.0), (
        f"scored {score} — that is the document-to-document value, which is "
        "systematically higher than what dedup sees at runtime")


def test_a_plain_callable_embedder_still_works(backfill, monkeypatch):
    """The offline stand-in is a bare function with no embed_query, and
    LangGraph treats it as an EmbeddingsFunc. The fallback branch keeps the
    script runnable against it rather than raising AttributeError."""
    monkeypatch.setattr(backfill.memory, "index_config",
                        lambda: {"embed": lambda texts: [[1.0, 0.0]
                                                         for _ in texts]})

    assert backfill._similarity(None, "a", "b") == pytest.approx(1.0)


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

    monkeypatch.setattr(backfill.memory, "get_store", lambda: Store())
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
