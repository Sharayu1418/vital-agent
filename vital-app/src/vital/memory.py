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

from vital import metrics

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


def anchor_of(value: dict) -> str:
    """The text a row was CREATED with, which never changes.

    Rows written before anchors existed do not have one. Falling back to the
    current fact makes those rows behave exactly as they did before rather
    than failing every comparison, so no migration is required — an old row
    simply gets the anchor rule applied from the next time it is touched.
    """
    return (value or {}).get("anchor") or (value or {}).get("fact") or ""


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

    A MERGE MUST CLEAR THE THRESHOLD TWICE — against the row's current text
    AND against its anchor.

    Requiring only the current text is single-linkage clustering, and single
    linkage chains. Because a merge overwrites the row it matched, the thing
    the next fact gets compared against is whatever landed there last, so a
    cluster walks: A absorbs B, then C is judged against B and absorbs the
    row, and A and C end up merged having never been compared. A probe with
    a controlled embedder collapsed a pair 0.697 apart at a 0.87 threshold —
    which is why eighteen distinct facts became ten in the retrieval eval.

    Raising the threshold does not help; it only makes the chain longer. The
    fix has to be structural. The anchor never moves, so it bounds how far a
    row can drift from where it started, while still letting a genuinely
    updated fact ("moved to Brooklyn") replace the one it supersedes.
    """
    hits = store.search(_ns(user_id), query=fact, limit=DEDUP_CANDIDATES)
    candidates = [(h.key, (h.value or {}).get("fact") or "",
                   anchor_of(h.value)) for h in hits]
    candidates = [(key, text, anchor) for key, text, anchor in candidates
                  if text]
    if not candidates:
        return None

    # One batched call for everything. Anchors are usually identical to the
    # current text — a row is only ever anchored to something different once
    # it has absorbed a fact — so embed the DISTINCT strings and look each
    # one up, rather than paying for the same text twice per candidate.
    wanted = [fact]
    for _, text, anchor in candidates:
        wanted += [text, anchor]
    distinct = list(dict.fromkeys(wanted))
    vectors = dict(zip(distinct, _as_documents(_embedder(store), distinct)))
    incoming = vectors[fact]

    threshold = settings().memory_dedup_threshold
    best_key, best_score = None, threshold
    for key, text, anchor in candidates:
        current_score = _cosine(incoming, vectors[text])
        if current_score < threshold:
            continue
        if _cosine(incoming, vectors[anchor]) < threshold:
            continue        # the row has drifted; this would be a chain
        if current_score >= best_score:
            best_key, best_score = key, current_score
    return best_key


def remember(store, user_id: str, transcript: str, llm) -> int:
    """Extract facts and store them, deduplicating. Returns #stored.

    An embedding failure skips the WRITE and leaves the conversation alone —
    memory must never break a turn. The miss surfaces through tool-health
    logging rather than the user.

    THAT SENTENCE USED TO BE FALSE, which is worth keeping written down.

    The except below swallowed the failure and logged NOTHING. The docstring
    claimed a safety net that had never been built, so a fact that failed to
    write left no trace anywhere — not in the logs, not in the return count's
    surroundings, nowhere. It cost a day: the retrieval eval stored 10 of 18
    facts and every hypothesis went to dedup, because dedup was the only part
    of the path that reported anything. After the chaining fix dropped merges
    from eight to one, ten facts were still missing seven that nothing had
    merged. They had been failing here the whole time, invisibly.

    A comment describing behaviour nobody implemented is worse than no
    comment, because it is where you stop looking.
    """
    result: FactList = llm.with_structured_output(FactList).invoke(
        EXTRACT_PROMPT.format(transcript=transcript))

    stored = 0
    for fact in result.facts:
        if fact.confidence < CONFIDENCE_FLOOR:
            continue
        try:
            key = duplicate_key(store, user_id, fact.fact)
            if key is None:
                key, anchor = uuid.uuid4().hex, fact.fact
            else:
                # Carry the original text forward. This is the whole point of
                # the anchor: if the merge overwrote it too, the row would
                # re-anchor to its newest text every time and chaining would
                # be back, just one write later.
                existing = store.get(_ns(user_id), key)
                anchor = anchor_of(existing.value if existing else None) \
                    or fact.fact
            store.put(_ns(user_id), key,
                      {"fact": fact.fact, "confidence": fact.confidence,
                       "anchor": anchor})
        except Exception as exc:
            # embedding/store failure: one fact goes unsaved, the turn is
            # untouched. Deliberately not a partial write — storing without
            # a vector would create a memory the agent can never retrieve.
            #
            # Swallowing it is still right — memory must not break a turn —
            # but swallowing it SILENTLY is what hid seven lost facts behind
            # a dedup investigation. Same shape as every tool failure in this
            # codebase, so it goes through the same channel and the same
            # alert on error rate.
            metrics.log_tool(user_id, "memory.remember", "error",
                             error=f"{type(exc).__name__}: {exc}")
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
