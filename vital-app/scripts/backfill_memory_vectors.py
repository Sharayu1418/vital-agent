"""One-off: re-embed existing memories and merge near-duplicates.

WHY THIS IS NEEDED
------------------
Memories written before semantic memory landed have no vector. The store's
similarity search only sees indexed rows, so those facts would remain
visible in the "What VITAL knows" panel while being permanently unreachable
by recall — shown to the user, never reaching an agent. Re-putting each one
re-embeds it.

It also merges what the old difflib dedup could not. Production accumulated
four rows for one fact:

    User is in Albany.
    User is located in or near Albany.
    User is located in Albany/Guilderland.
    User is interested in Albany.

USAGE
-----
Dry run first — prints what WOULD change, writes nothing:

    uv run python scripts/backfill_memory_vectors.py

Then, once the output looks right:

    uv run python scripts/backfill_memory_vectors.py --apply

Needs DATABASE_URL and Vertex credentials in the environment, exactly like
the app. Safe to re-run: the second pass finds nothing left to merge.

NOTE: run this AFTER deploying the semantic-memory change, so the store is
already indexed.
"""
import argparse
import sys
from functools import lru_cache

sys.path.insert(0, "src")

from vital import memory                                    # noqa: E402
from vital.config import settings                           # noqa: E402


def namespaces(store) -> list[tuple]:
    """Every (user_id, 'profile') namespace holding memories."""
    seen = set()
    for ns in store.list_namespaces(prefix=None):
        if ns and ns[-1] == memory.NAMESPACE_SUFFIX:
            seen.add(tuple(ns))
    return sorted(seen)


def plan_for_user(store, user_id: str) -> tuple[list, list]:
    """Returns (kept, merged) without writing anything.

    Facts are processed most-confident first, so when two rows say the same
    thing the better-attested wording survives.
    """
    rows = memory.all_memories(store, user_id)
    rows.sort(key=lambda r: r.get("confidence", 0), reverse=True)

    kept: list[dict] = []
    merged: list[tuple[str, str]] = []
    threshold = settings().memory_dedup_threshold

    for row in rows:
        duplicate_of = None
        for keeper in kept:
            score = _similarity(store, keeper["fact"], row["fact"])
            if score is not None and score >= threshold:
                duplicate_of = keeper["fact"]
                break
        if duplicate_of:
            merged.append((row["fact"], duplicate_of))
        else:
            kept.append(row)
    return kept, merged


@lru_cache
def _embedder():
    """One client for the whole run. index_config() builds a fresh one each
    call, and this is invoked per candidate pair."""
    return memory.index_config()["embed"]


def _similarity(store, keeper: str, candidate: str) -> float | None:
    """Similarity as the APP computes it: the surviving fact as a DOCUMENT,
    the incoming one as a QUERY.

    This asymmetry is not a detail. At runtime dedup goes through
    store.search(query=...), so it compares RETRIEVAL_QUERY against
    RETRIEVAL_DOCUMENT vectors. text-embedding-004 is task-typed and those
    score ~0.24 LOWER than document-to-document for the same pair.

    Embedding both sides as documents here — which this script used to do —
    inflates every score onto a scale MEMORY_DEDUP_THRESHOLD was never
    calibrated for. Measured 5 Aug 2026, the most similar pair that must NOT
    merge scores 0.559 on the query path but 0.800 on the document path. At
    a threshold of 0.63 that flips the script from correct to merging every
    distinct fact it sees — silent data loss across the whole store, in the
    one operation that deletes rows.
    """
    import math

    embed = _embedder()
    if hasattr(embed, "embed_documents"):
        doc = embed.embed_documents([keeper])[0]
        query = embed.embed_query(candidate)
    else:                                    # offline stand-in: one callable
        doc, query = embed([keeper, candidate])
    dot = sum(x * y for x, y in zip(doc, query))
    nd = math.sqrt(sum(x * x for x in doc)) or 1.0
    nq = math.sqrt(sum(x * x for x in query)) or 1.0
    return dot / (nd * nq)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without it this is a dry run")
    args = parser.parse_args()

    if not settings().database_url:
        print("DATABASE_URL is not set — nothing to back fill "
              "(local runs use an in-memory store that starts empty).")
        return 1

    store = memory.get_store()
    spaces = namespaces(store)
    if not spaces:
        print("No memory namespaces found.")
        return 0

    total_kept = total_merged = 0
    for ns in spaces:
        user_id = ns[0]
        kept, merged = plan_for_user(store, user_id)
        total_kept += len(kept)
        total_merged += len(merged)

        print(f"\n{user_id}: {len(kept) + len(merged)} rows "
              f"-> {len(kept)} kept, {len(merged)} merged")
        for fact, into in merged:
            print(f"    merge  {fact!r}")
            print(f"      into {into!r}")

        if not args.apply:
            continue

        # Rewrite the namespace: delete everything, re-put the survivors so
        # each gets a fresh embedding. Deletes come first so a re-run is
        # idempotent rather than accumulating.
        for row in memory.all_memories(store, user_id):
            memory.forget(store, user_id, row["key"])
        for row in kept:
            store.put(memory._ns(user_id), row["key"],
                      {"fact": row["fact"],
                       "confidence": row.get("confidence", 0.9)})

    verb = "Wrote" if args.apply else "Would write"
    print(f"\n{verb}: {total_kept} facts kept, {total_merged} merged away.")
    if not args.apply:
        print("Dry run — nothing changed. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
