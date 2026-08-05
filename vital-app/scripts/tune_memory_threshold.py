"""Measure real embedding similarities and derive the dedup threshold.

MEMORY_DEDUP_THRESHOLD decides when a new fact REPLACES an existing one.
Guessing it is how the first attempt shipped 0.82, which left all four
Albany variants in place.

There are two failure directions and they pull opposite ways:

  too HIGH -> duplicates survive          (the reported bug)
  too LOW  -> distinct memories get eaten (worse: silent data loss)

So a usable threshold has to sit BELOW the least-similar pair that should
merge, and ABOVE the most-similar pair that must not. This prints both
numbers. If they overlap, no single threshold works and the honest answer
is that similarity alone can't separate these cases.

    MEMORY_LIVE_EVAL=1 uv run python scripts/tune_memory_threshold.py

Needs Vertex credentials. Costs a handful of embedding calls.
"""
import itertools
import math
import os
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def main() -> int:
    from test_memory_live import ALBANY_DUPLICATES, DISTINCT_PAIRS
    from vital import memory

    embed = memory.index_config()["embed"]

    def vectors(texts):
        if hasattr(embed, "embed_documents"):
            return embed.embed_documents(list(texts))
        return embed(list(texts))

    print("\nSHOULD MERGE — variants of one fact")
    print("-" * 64)
    vecs = vectors(ALBANY_DUPLICATES)
    merge_scores = []
    for (i, a), (j, b) in itertools.combinations(enumerate(ALBANY_DUPLICATES), 2):
        score = cosine(vecs[i], vecs[j])
        merge_scores.append(score)
        print(f"  {score:.3f}  {a[:34]:36} | {b[:34]}")

    print("\nMUST NOT MERGE — genuinely different facts")
    print("-" * 64)
    keep_scores = []
    for a, b in DISTINCT_PAIRS:
        va, vb = vectors([a, b])
        score = cosine(va, vb)
        keep_scores.append(score)
        print(f"  {score:.3f}  {a[:34]:36} | {b[:34]}")

    lowest_merge = min(merge_scores)
    highest_keep = max(keep_scores)

    print("\n" + "=" * 64)
    print(f"  lowest  'should merge'  similarity : {lowest_merge:.3f}")
    print(f"  highest 'must not merge' similarity: {highest_keep:.3f}")

    if lowest_merge <= highest_keep:
        print("\n  OVERLAP — no single threshold separates these cases.")
        print("  Similarity alone is not enough; the honest options are to")
        print("  accept some duplicates (favour the safer direction) or to")
        print("  dedupe on something richer than one cosine score.")
        print(f"\n  Safest available value: {highest_keep + 0.01:.2f}")
        print("  (protects distinct facts; some duplicates will survive)")
        return 1

    suggested = (lowest_merge + highest_keep) / 2
    print(f"\n  Suggested MEMORY_DEDUP_THRESHOLD: {suggested:.2f}")
    print(f"  (clear gap of {lowest_merge - highest_keep:.3f} between the two)")
    print("=" * 64 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
