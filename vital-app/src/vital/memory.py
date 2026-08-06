"""Long-term memory over LangGraph Store (Phase 2B).

Principles (from phase doc):
- Only STABLE facts ("lives in Brooklyn", "hates gyms") — never transient
  state ("is tired today"). The extractor prompt enforces this; the
  confidence floor catches the rest.
- Update-don't-duplicate: near-matching facts overwrite the existing key.
- Aggressive filtering beats big memory: retrieval quality degrades with
  junk, so we'd rather miss a fact than store noise.

Store backend: InMemoryStore locally; PostgresStore when DATABASE_URL is
set (same swap pattern as the checkpointer). Both are configured with a
vector index, so retrieval and dedup are SEMANTIC.

Why: the previous implementation compared words. Retrieval ranked by raw
word overlap, so "ceramics" never found a stored "pottery" fact, and every
fact starting with "User " matched everything. Dedup used a difflib ratio,
which reads "User is in Albany" and "User is located in or near Albany" as
different strings — production accumulated four rows for one fact.

LangGraph's store does the vector work (pgvector under PostgresStore), so
there is no bespoke schema here: an IndexConfig, and search(query=...)
becomes a similarity search.
"""
import atexit
import uuid
from contextlib import ExitStack
from functools import lru_cache

from pydantic import BaseModel, Field

from vital.config import settings

NAMESPACE_SUFFIX = "profile"
CONFIDENCE_FLOOR = 0.6

_RESOURCE_STACK = ExitStack()
atexit.register(_RESOURCE_STACK.close)

EXTRACT_PROMPT = """Extract STABLE personal facts about the user from this \
conversation snippet, if any.

Stable: city, age group, interests, dislikes, constraints (budget, schedule,
health conditions they volunteer), preferences that will still be true next month.
NOT stable: today's mood, tonight's plan, one-off requests, anything the
assistant said, anything speculative.

Return an empty list when nothing qualifies — that is the most common
correct answer.

Conversation:
{transcript}"""


class Fact(BaseModel):
    fact: str = Field(description="One short sentence, third person: 'User ...'")
    confidence: float = Field(ge=0, le=1)


class FactList(BaseModel):
    facts: list[Fact]


def _embeddings():
    """The real embedding model. Isolated so tests patch THIS and never the
    network — same seam pattern as security._firebase_verify and the crisis
    classifier."""
    from langchain_google_vertexai import VertexAIEmbeddings

    cfg = settings()
    return VertexAIEmbeddings(model_name=cfg.embedding_model,
                              project=cfg.google_cloud_project,
                              location=cfg.google_cloud_location)


def index_config() -> dict:
    """Vector index for the store. `fields` limits embedding to the fact
    text — embedding the confidence number too would just add noise."""
    cfg = settings()
    return {"dims": cfg.embedding_dims, "embed": _embeddings(),
            "fields": ["fact"]}


@lru_cache
def get_store():
    cfg = settings()
    if cfg.database_url:
        from langgraph.store.postgres import PostgresStore
        store = _RESOURCE_STACK.enter_context(
            PostgresStore.from_conn_string(cfg.database_url,
                                           index=index_config())
        )
        # Creates the vector extension and tables. Needs the DB user to have
        # rights to CREATE EXTENSION; if the deploy fails at startup, run
        # `CREATE EXTENSION IF NOT EXISTS vector;` by hand once.
        store.setup()
        return store
    from langgraph.store.memory import InMemoryStore
    return InMemoryStore(index=index_config())


def _ns(user_id: str) -> tuple:
    return (user_id, NAMESPACE_SUFFIX)


def _embedder(via=None):
    """Resolve an embedder from a store, an Embeddings, or nothing.

    Preferring the STORE's own embedder matters: it is the one that produced
    the stored vectors, so we can never score against a different model than
    the one that wrote the data.
    """
    if via is None:
        return index_config()["embed"]
    return getattr(via, "embeddings", None) or via


def _as_documents(embed, texts: list[str]) -> list[list[float]]:
    """Embed every text the SAME way. LangGraph wraps plain callables in
    EmbeddingsLambda, so the method form is the normal path; the callable
    branch is for test doubles that skip that wrapping."""
    if hasattr(embed, "embed_documents"):
        return embed.embed_documents(list(texts))
    return embed(list(texts))


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def similarity(a: str, b: str, via=None) -> float:
    """Similarity between two facts, on the ONE scale the threshold means.

    Both texts are embedded as documents, so the measure is symmetric and
    identical everywhere it is used — dedup, the backfill script, the
    tuner, the live eval. Anything that reasons about
    MEMORY_DEDUP_THRESHOLD must call this rather than compute its own; four
    separate re-implementations of "cosine between two facts" are what let
    the tuner, the tests and production drift onto three different scales.

    `via` may be a store or an Embeddings; omit it and a fresh embedder is
    built, which is convenient for scripts and wasteful in a loop.
    """
    va, vb = _as_documents(_embedder(via), [a, b])
    return _cosine(va, vb)


# How many near neighbours to re-score. The store's ranking only has to get
# the right fact into this window; the merge decision is made below.
DEDUP_CANDIDATES = 5


def duplicate_key(store, user_id: str, fact: str) -> str | None:
    """Key of an existing fact this one should REPLACE, or None.

    Semantic, not textual. difflib read "User is in Albany" and "User is
    located in or near Albany" as different strings and production ended up
    with four rows for one fact.

    The store ranks candidates; it does NOT get to decide. Its `score` is
    deliberately ignored, because the two backends do not compute it the
    same way: InMemoryStore embeds the search query with embed_query, while
    PostgresStore 3.1.0 embeds it with embed_documents. text-embedding-004
    is task-typed, so for the same pair those differ by ~0.24 — which meant
    a threshold calibrated against the tests (InMemoryStore) collapsed every
    distinct fact into one row in production (Postgres). Re-scoring here
    makes the decision depend only on the model, never on the backend.
    """
    hits = store.search(_ns(user_id), query=fact, limit=DEDUP_CANDIDATES)
    candidates = [(h.key, (h.value or {}).get("fact") or "") for h in hits]
    candidates = [(key, text) for key, text in candidates if text]
    if not candidates:
        return None

    # One batched call: the incoming fact plus every candidate, all as
    # documents.
    vectors = _as_documents(_embedder(store),
                            [fact] + [text for _, text in candidates])
    incoming, stored = vectors[0], vectors[1:]

    best_key, best_score = None, settings().memory_dedup_threshold
    for (key, _), vector in zip(candidates, stored):
        score = _cosine(incoming, vector)
        if score >= best_score:
            best_key, best_score = key, score
    return best_key


def remember(store, user_id: str, transcript: str, llm) -> int:
    """Extract facts and store them, deduplicating. Returns #stored.

    An embedding failure skips the WRITE and leaves the conversation alone —
    memory must never break a turn. The miss surfaces through tool-health
    logging rather than the user.
    """
    result: FactList = llm.with_structured_output(FactList).invoke(
        EXTRACT_PROMPT.format(transcript=transcript))

    stored = 0
    for fact in result.facts:
        if fact.confidence < CONFIDENCE_FLOOR:
            continue
        try:
            key = duplicate_key(store, user_id, fact.fact) or uuid.uuid4().hex
            store.put(_ns(user_id), key,
                      {"fact": fact.fact, "confidence": fact.confidence})
        except Exception:
            # embedding/store failure: one fact goes unsaved, the turn is
            # untouched. Deliberately not a partial write — storing without
            # a vector would create a memory the agent can never retrieve.
            continue
        stored += 1
    return stored


def recall(store, user_id: str, query: str, limit: int | None = None) -> list[str]:
    """Semantic retrieval: facts closest in meaning to what the user just
    said. Word overlap could not do this — every fact begins with "User ",
    stopwords matched everything, and "ceramics" never found "pottery".

    Falls back to an unranked read if the vector search fails, because a
    few profile facts are better context than none.
    """
    limit = limit or settings().memory_recall_limit
    try:
        hits = store.search(_ns(user_id), query=query, limit=limit)
        return [h.value["fact"] for h in hits]
    except Exception:
        items = list(store.search(_ns(user_id)))[:limit]
        return [i.value["fact"] for i in items]


def all_memories(store, user_id: str) -> list[dict]:
    return [{"key": i.key, **i.value} for i in store.search(_ns(user_id))]


def forget(store, user_id: str, key: str) -> None:
    store.delete(_ns(user_id), key)
