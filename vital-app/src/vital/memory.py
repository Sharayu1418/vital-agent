"""Long-term memory over LangGraph Store (Phase 2B).

Principles (from phase doc):
- Only STABLE facts ("lives in Brooklyn", "hates gyms") — never transient
  state ("is tired today"). The extractor prompt enforces this; the
  confidence floor catches the rest.
- Update-don't-duplicate: near-matching facts overwrite the existing key.
- Aggressive filtering beats big memory: retrieval quality degrades with
  junk, so we'd rather miss a fact than store noise.

Store backend: InMemoryStore locally; PostgresStore when DATABASE_URL is
set (same swap pattern as the checkpointer). Retrieval is keyword-overlap
for now — pgvector semantic search is the Phase-4/5 upgrade, behind this
same interface.
"""
import atexit
import difflib
import uuid
from contextlib import ExitStack
from functools import lru_cache

from pydantic import BaseModel, Field

from vital.config import settings

NAMESPACE_SUFFIX = "profile"
SIMILARITY_OVERWRITE = 0.8
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


@lru_cache
def get_store():
    cfg = settings()
    if cfg.database_url:
        from langgraph.store.postgres import PostgresStore
        store = _RESOURCE_STACK.enter_context(
            PostgresStore.from_conn_string(cfg.database_url)
        )
        store.setup()
        return store
    from langgraph.store.memory import InMemoryStore
    return InMemoryStore()


def _ns(user_id: str) -> tuple:
    return (user_id, NAMESPACE_SUFFIX)


def remember(store, user_id: str, transcript: str, llm) -> int:
    """Extract facts and store them, deduplicating. Returns #stored."""
    result: FactList = llm.with_structured_output(FactList).invoke(
        EXTRACT_PROMPT.format(transcript=transcript))
    existing = {item.key: item.value["fact"] for item in store.search(_ns(user_id))}

    stored = 0
    for fact in result.facts:
        if fact.confidence < CONFIDENCE_FLOOR:
            continue
        # near-duplicate? overwrite that key instead of adding a sibling
        target_key = None
        for key, old in existing.items():
            ratio = difflib.SequenceMatcher(None, fact.fact.lower(), old.lower()).ratio()
            if ratio >= SIMILARITY_OVERWRITE:
                target_key = key
                break
        key = target_key or uuid.uuid4().hex
        store.put(_ns(user_id), key, {"fact": fact.fact, "confidence": fact.confidence})
        existing[key] = fact.fact
        stored += 1
    return stored


def recall(store, user_id: str, query: str, limit: int | None = None) -> list[str]:
    """Keyword-overlap retrieval. Facts sharing words with the query rank
    first; with no overlap at all we return the most confident facts, since
    a handful of good profile facts is almost always relevant context."""
    limit = limit or settings().memory_recall_limit
    items = list(store.search(_ns(user_id)))
    if not items:
        return []
    q_words = set(query.lower().split())

    def score(item):
        f_words = set(item.value["fact"].lower().split())
        return (len(q_words & f_words), item.value.get("confidence", 0))

    ranked = sorted(items, key=score, reverse=True)
    return [i.value["fact"] for i in ranked[:limit]]


def all_memories(store, user_id: str) -> list[dict]:
    return [{"key": i.key, **i.value} for i in store.search(_ns(user_id))]


def forget(store, user_id: str, key: str) -> None:
    store.delete(_ns(user_id), key)
