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


def _similarity(store, keeper: str, candidate: str) -> float | None:
    """Delegates to the app's own function — deliberately.

    This script used to carry its own cosine implementation, and that is
    precisely how it ended up on a different scale from the code it was
    meant to predict. A bulk delete driven by a second opinion about
    similarity is not a bulk delete anyone can review.
    """
    return memory.similarity(keeper, candidate, via=store)


def _report_drift(store, user_id: str) -> None:
    """How far rows have moved from the text they were created with.

    Dedup used to be single-linkage: a merge overwrote the row it matched,
    so the next fact was compared against whatever landed there last and a
    row could walk away from its origin, absorbing facts it was never
    measured against. Rows now carry an immutable `anchor` and a merge has
    to clear the threshold against both.

    Two honest limits on what this can tell you.

    A row with no anchor predates the fix. It may or may not have absorbed
    something; the absorbed text was overwritten, so there is nothing left
    to detect it with. Those losses are NOT RECOVERABLE and this report will
    never surface them — the count of un-anchored rows is the size of the
    blind spot, not the size of the damage.

    A row whose anchor differs from its fact has absorbed at least one
    merge. That is normal and usually correct: "moved to Brooklyn" SHOULD
    replace "lives in Albany". It is only worth a look when the similarity
    is low, which now cannot happen going forward, so a low number here is
    the thing to check after the fix ships.
    """
    rows = memory.all_memories(store, user_id)
    unanchored = [r for r in rows if not r.get("anchor")]
    drifted = [r for r in rows
               if r.get("anchor") and r["anchor"] != r["fact"]]

    if unanchored:
        print(f"    {len(unanchored)} row(s) predate anchoring — any facts "
              "they absorbed are unrecoverable and invisible here")
    if not drifted:
        return
    print(f"    {len(drifted)} row(s) have absorbed a merge:")
    for row in drifted:
        score = _similarity(store, row["anchor"], row["fact"])
        flag = "  <-- below threshold, would not merge today" if (
            score is not None
            and score < settings().memory_dedup_threshold) else ""
        shown = "n/a" if score is None else f"{score:.3f}"
        print(f"      {row['fact']!r}")
        print(f"        anchored to {row['anchor']!r} ({shown}){flag}")


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
        _report_drift(store, user_id)

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
                       "confidence": row.get("confidence", 0.9),
                       # A survivor of THIS pass is anchored to its own text.
                       # Any anchor it carried belonged to a row that has just
                       # been re-clustered from scratch, so keeping it would
                       # anchor the row to a comparison that no longer
                       # happened.
                       "anchor": row["fact"]})

    verb = "Wrote" if args.apply else "Would write"
    print(f"\n{verb}: {total_kept} facts kept, {total_merged} merged away.")
    if not args.apply:
        print("Dry run — nothing changed. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
