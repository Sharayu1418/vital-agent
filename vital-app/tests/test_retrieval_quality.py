"""How good is memory retrieval, actually?

WHAT WAS MISSING
----------------
test_memory.py proves the MECHANISM works: similarity drives dedup, the
threshold is honoured, failures degrade safely. test_memory_live.py proves
the threshold separates duplicates from distinct facts.

Neither asks the question that matters at runtime: **when the agent looks
something up, does it get the right fact back?**

That is the most common gap in RAG projects. Everyone tests that the vector
store returns something. Almost nobody tests that it returns the right
thing — and a retriever that quietly returns the wrong three facts produces
answers that are confidently, invisibly worse.

    RETRIEVAL_EVAL=1 uv run pytest tests/test_retrieval_quality.py -s

THE METRICS
-----------
**recall@k** — was a correct fact in the top k? This is what actually
matters, because `memory_recall_limit` facts get injected into the prompt
and the agent sees all of them. If the right fact is at position 4 of 5,
the agent still gets it.

**MRR** (mean reciprocal rank) — 1/position of the first correct fact,
averaged. Rewards putting the right answer first. Useful because recall@5
can look fine while everything correct sits at the bottom, which is a
retriever that is one config change away from failing.

Reported together on purpose: recall alone hides ranking quality, MRR alone
hides whether the fact was found at all.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from retrieval_cases import CORPUS, QUERIES

LIVE = os.environ.get("RETRIEVAL_EVAL") == "1"

# Gates. Deliberately not 100%: some queries are genuinely ambiguous, and a
# bar nobody can clear gets lowered until it means nothing. These are set
# to catch REGRESSION — a change that drops retrieval — rather than to
# assert the retriever is perfect.
MIN_RECALL_AT_5 = 0.80
MIN_RECALL_AT_1 = 0.50
MIN_MRR = 0.60


# ---------- the set itself, checked in CI ----------

def test_every_expected_fact_exists_in_the_corpus():
    """The classic broken-eval bug: an expected answer that was never
    indexed. Recall is then zero by construction and you spend an afternoon
    tuning a retriever that was working."""
    corpus = set(CORPUS)
    for query, expected in QUERIES:
        for fact in expected:
            assert fact in corpus, (
                f"{query!r} expects {fact!r}, which is not in the corpus — "
                "this query can never pass")


def test_the_corpus_is_bigger_than_the_result_window():
    """recall@5 against four facts is 100% for a retriever that returns
    everything. The corpus has to be large enough that retrieval is a
    choice."""
    assert len(CORPUS) >= 15


def test_queries_do_not_simply_repeat_the_fact():
    """If the query shares most of its words with the answer, this measures
    keyword matching and would pass on a retriever that cannot do synonyms
    at all — which is the thing semantic memory exists for."""
    for query, expected in QUERIES:
        query_words = {w.strip(".,?'") for w in query.lower().split()
                       if len(w) > 3}
        for fact in expected:
            fact_words = {w.strip(".,?'") for w in fact.lower().split()
                          if len(w) > 3} - {"user"}
            overlap = query_words & fact_words
            assert len(overlap) <= 1, (
                f"{query!r} shares {overlap} with its answer — too easy to "
                "distinguish semantic retrieval from keyword matching")


def test_there_are_enough_queries_to_mean_something():
    assert len(QUERIES) >= 12


# ---------- metrics ----------

def recall_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    """1.0 if any expected fact is in the top k.

    Any, not all: the agent receives every retrieved fact, so finding one
    correct fact is what unblocks the answer. Requiring all of them would
    punish queries that legitimately have two right answers.
    """
    return 1.0 if set(retrieved[:k]) & set(expected) else 0.0


def reciprocal_rank(retrieved: list[str], expected: list[str]) -> float:
    for position, fact in enumerate(retrieved, start=1):
        if fact in expected:
            return 1.0 / position
    return 0.0


def test_every_live_eval_flag_disables_the_offline_stand_ins():
    """The bug this eval hit on its first run.

    conftest patches in a 16-dimension bag-of-words embedder so the offline
    suite never touches a network. It only un-patched itself under
    MEMORY_LIVE_EVAL, and this eval uses RETRIEVAL_EVAL — so a "live" run
    scored the stand-in. Half the facts looked alike, dedup merged eight of
    eighteen, and the numbers would have been fiction.

    Every module that gates on an env var must have that var registered, or
    the next eval silently measures the fake. Scanning for the flags rather
    than listing them means a new eval is covered the day it is written.
    """
    import pathlib
    import re

    from conftest import LIVE_EVAL_FLAGS

    tests_dir = pathlib.Path(__file__).resolve().parent
    used = set()
    for path in tests_dir.glob("test_*.py"):
        for match in re.finditer(r'environ\.get\("([A-Z_]*(?:EVAL|EVALS))"\)',
                                 path.read_text()):
            used.add(match.group(1))

    unregistered = used - set(LIVE_EVAL_FLAGS)
    assert not unregistered, (
        f"{sorted(unregistered)} gate a live eval but are not in "
        "conftest.LIVE_EVAL_FLAGS, so those runs would score the offline "
        "stand-in and report it as a real result")


def test_the_metrics_are_right():
    """Guard the guard. A scoring bug would make every later number a
    fiction, and there is nothing to compare it against."""
    assert recall_at_k(["a", "b", "c"], ["c"], 3) == 1.0
    assert recall_at_k(["a", "b", "c"], ["c"], 2) == 0.0
    assert recall_at_k([], ["c"], 5) == 0.0
    assert reciprocal_rank(["a", "b", "c"], ["c"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(["c", "a"], ["c"]) == 1.0
    assert reciprocal_rank(["a", "b"], ["z"]) == 0.0


# ---------- the live run ----------

@pytest.mark.skipif(not LIVE, reason="set RETRIEVAL_EVAL=1")
def test_retrieval_meets_the_gates(live_project):
    """Seeds a store with the real embedder and measures what comes back.

    Uses memory.remember and memory.recall — the actual production
    functions — rather than calling the store directly. A harness that
    talks to the store instead of the app measures a path nobody runs,
    which is the mistake this codebase has made repeatedly.
    """
    from langgraph.store.memory import InMemoryStore

    from vital import memory

    store = InMemoryStore(index=memory.index_config())
    merges, failures = _seed_and_record_merges(memory, store)

    stored = {m["fact"] for m in memory.all_memories(store, "eval-user")}
    if merges:
        threshold = memory.settings().memory_dedup_threshold
        print(f"\n  {len(merges)} facts merged at threshold {threshold}, "
              "recorded AT WRITE TIME:")
        for incoming, target, similarity in merges:
            print(f"    {incoming}")
            print(f"      -> matched {target}  ({similarity:.3f})")
    if failures:
        print(f"\n  {len(failures)} facts FAILED TO WRITE:")
        for fact in failures:
            print(f"    {fact}")

    # Account for every fact before drawing any conclusion. The first version
    # of this asserted only on the total and blamed dedup in the message —
    # so when writes started failing, the eval confidently reported a dedup
    # problem. Seven facts were lost to a silent `except` for a day because
    # the harness named a cause instead of reporting a number.
    assert not failures, (
        f"{len(failures)} of {len(CORPUS)} facts failed to write. This is NOT "
        "a dedup problem — memory.remember logs the reason under "
        'metric="tool_call", tool="memory.remember". Read that before '
        "touching the threshold.")
    assert len(stored) + len(merges) == len(CORPUS), (
        f"{len(stored)} stored + {len(merges)} merged != {len(CORPUS)} "
        "seeded — facts are disappearing through a path this harness cannot "
        "see, which is the one situation where every number below is fiction")
    assert len(stored) >= len(CORPUS) - 2, (
        f"only {len(stored)} of {len(CORPUS)} facts stored — dedup is merging "
        "distinct facts, which would make every number below meaningless")

    at1 = at3 = at5 = mrr = 0.0
    misses = []
    for query, expected in QUERIES:
        retrieved = memory.recall(store, "eval-user", query, limit=5)
        at1 += recall_at_k(retrieved, expected, 1)
        at3 += recall_at_k(retrieved, expected, 3)
        at5 += recall_at_k(retrieved, expected, 5)
        rank = reciprocal_rank(retrieved, expected)
        mrr += rank
        if rank == 0.0:
            misses.append((query, expected[0], retrieved[:3]))

    n = len(QUERIES)
    at1, at3, at5, mrr = at1 / n, at3 / n, at5 / n, mrr / n

    print(f"\n  {n} queries against {len(stored)} facts")
    print(f"  recall@1 {at1:.0%}   recall@3 {at3:.0%}   recall@5 {at5:.0%}")
    print(f"  MRR      {mrr:.2f}")
    if misses:
        print(f"\n  {len(misses)} complete misses:")
        for query, wanted, got in misses:
            print(f"    {query!r}")
            print(f"      wanted: {wanted}")
            print(f"      got:    {got}")

    assert at5 >= MIN_RECALL_AT_5, f"recall@5 {at5:.0%} below {MIN_RECALL_AT_5:.0%}"
    assert at1 >= MIN_RECALL_AT_1, f"recall@1 {at1:.0%} below {MIN_RECALL_AT_1:.0%}"
    assert mrr >= MIN_MRR, f"MRR {mrr:.2f} below {MIN_MRR}"


@pytest.mark.skipif(not LIVE, reason="set RETRIEVAL_EVAL=1")
def test_the_user_prefix_is_not_flattening_similarity(live_project):
    """Every stored fact begins with "User ".

    That is a real hypothesis worth checking rather than assuming: if the
    shared prefix dominates the embedding, unrelated facts drift toward
    each other and retrieval degrades toward random. This measures how far
    apart unrelated facts actually sit, with and without the prefix.

    Diagnostic, not a gate. It either shows the prefix is harmless or gives
    a concrete reason to strip it — and "we should probably remove the
    prefix" is not worth acting on without a number.
    """
    import itertools

    from vital import memory

    embed = memory.index_config()["embed"]
    unrelated = ["User is into pottery and ceramics.",
                 "User has a bad knee and avoids running.",
                 "User is learning Spanish.",
                 "User's partner works nights."]
    stripped = [f.replace("User is ", "").replace("User has ", "")
                 .replace("User's ", "").replace("User ", "")
                for f in unrelated]

    with_prefix = [memory.similarity(a, b, via=embed)
                   for a, b in itertools.combinations(unrelated, 2)]
    without = [memory.similarity(a, b, via=embed)
               for a, b in itertools.combinations(stripped, 2)]

    mean_with = sum(with_prefix) / len(with_prefix)
    mean_without = sum(without) / len(without)
    print("\n  mean similarity between UNRELATED facts")
    print(f"    with 'User' prefix:    {mean_with:.3f}")
    print(f"    without:               {mean_without:.3f}")
    print(f"    difference:            {mean_with - mean_without:+.3f}")
    if mean_with - mean_without > 0.05:
        print("    -> the prefix is measurably flattening similarity;"
              " worth storing facts without it")


def _seed_and_record_merges(memory, store):
    """Write the corpus, recording every merge AS IT HAPPENS.

    The first version of this reconstructed merges afterwards by finding
    each lost fact's nearest surviving neighbour. That gave nonsense —
    "learning Spanish merged into rock climbing at 0.804", below the
    threshold, which cannot happen.

    The reconstruction was wrong because a merge OVERWRITES the row it
    matched. So a row can be matched, overwritten, matched again by
    something else, and overwritten again. Afterwards you can only see the
    last text in that row, not the text that was actually compared against.
    Inferring the cause from the end state gave a confident wrong answer —
    which is the same mistake as every other harness bug in this project.

    Wrapping duplicate_key records the real comparison: what came in, what
    it matched, and the score at that moment.
    """
    recorded: list[tuple[str, str, float]] = []
    failures: list[str] = []
    original = memory.duplicate_key

    def watched(store_arg, user_id, fact):
        key = original(store_arg, user_id, fact)
        if key is not None:
            existing = {m["key"]: m["fact"]
                        for m in memory.all_memories(store_arg, user_id)}
            target = existing.get(key, "<unknown>")
            embed = memory.index_config()["embed"]
            recorded.append((fact, target,
                             memory.similarity(target, fact, via=embed)))
        return key

    memory.duplicate_key = watched
    try:
        for fact in CORPUS:
            # remember() returns the number it actually stored. Zero means
            # the write raised and was swallowed — which is survivable in
            # production and fatal to an eval, because the fact is simply
            # absent and every explanation points at dedup instead.
            if memory.remember(store, "eval-user", "…",
                               _fixed_extractor(memory, fact)) == 0:
                failures.append(fact)
    finally:
        memory.duplicate_key = original
    return recorded, failures


def _fixed_extractor(memory_mod, fact_text):
    """An extractor that returns one known fact, so the corpus is exactly
    what we intended rather than whatever a model decided to write."""
    class One:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, _prompt):
            return memory_mod.FactList(
                facts=[memory_mod.Fact(fact=fact_text, confidence=0.9)])

    return One()
