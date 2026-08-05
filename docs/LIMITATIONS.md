# Limitations and deliberate omissions

Things VITAL does not do, and why. Written down so they don't get
re-litigated from scratch, and so the reasoning is auditable when the
tradeoffs change.

---

## No third-party community-search provider

**Decision:** community discovery runs on Google Places and the Activity
Buddy Board. There is no Reddit, Meetup, Facebook or Discord integration,
and adding one should clear a high bar.

### What happened

The People Connector originally called Reddit's keyless
`/subreddits/search.json`. Reddit blocks datacenter IPs, so **every call
from Cloud Run failed**. The tool degraded gracefully — returning an
`error` key, which the agent faithfully reported as "community search is
temporarily down" — and so a completely dead feature looked like a
transient outage for months.

Fixing it properly meant app-only OAuth. That work was done and tested
(commit `e0db019`), but the credentials could not be obtained: as of 2026
Reddit closed self-service API registration. Every new app needs manual
approval under the [Responsible Builder
Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy),
approvals skew toward established commercial applicants, and — at time of
writing — the request form itself was broken (an Alpine.js error left the
submit button with no click handler).

### Why not just use a different provider

Because the whole category has closed:

| Provider | Status |
| --- | --- |
| Eventbrite public event search | endpoint removed, 2019 |
| Facebook Groups | deprecated for third parties, 2020 |
| Meetup | open REST API retired; meaningful access behind paid Pro, 2023+ |
| Reddit | approval-only, 2026 |
| Strava clubs | endpoints removed 1 Sept 2026; API now requires a subscription |
| Discord | never had a public directory API |

Six for six in six years. That is not bad luck — community and social-graph
data became strategically valuable, so platforms gated it. The base rate
for "free third-party community API still working in two years" is close
to zero, and each failure is *silent* when the caller degrades gracefully.

### The rule this implies

Sort external dependencies into two classes:

- **Commoditised infrastructure** — geo, maps, weather, places, ticketing.
  Multiple vendors, stable pricing, no strategic reason to close.
- **Platform-gated social data** — who belongs to what, who talks to whom.
  Single-source, strategically valuable, reliably closes.

Build on the first. Own the second. VITAL's durable tools (Places,
OpenWeather, Ticketmaster) are all class one; the one that died was class
two; and the Activity Buddy Board is class two that we own outright.

### What replaced it

1. **`search_places`** — where an activity actually happens is where its
   community is. Global coverage, already integrated. The People Connector
   searches gathering places specifically ("bouldering gym", "run club"),
   not generic venues.
2. **The Activity Buddy Board** — the only source of actual people, and
   the one asset here nobody else has. Its cold-start problem is a product
   problem (seeding, prompting), not something another API would solve.

The agent is explicitly forbidden from naming online communities from
model knowledge — it has no tool that can verify they exist, so an
unverifiable suggestion would violate the grounding principle. If the live
tools return little, it says so and offers the buddy board.

### If this changes

Reddit OAuth is a cherry-pick away: `git show e0db019`. It includes token
caching, graceful fallback, and six tests. Re-adding it means restoring
`vital/tools/communities.py`, the `reddit_*` settings, and the tool in
`people_connector.build_agent()`.

### Known consequence

Activity Scout and People Connector now both call Places. They stay
distinct by *intent* — "things to do" versus "where people gather" — but
the underlying data is identical. If routing between them proves unclear
in practice, the honest fix is merging the two agents, not inventing a
distinction the data doesn't support.

---

## Other current boundaries

- Approved plans commit to VITAL's relational calendar, not Google
  Calendar.
- Conversation history is trimmed to the most recent turns
  (`HISTORY_LIMIT`) but not *summarised*, so a very long thread loses early
  context rather than compressing it. Durable facts survive in long-term
  memory, which is injected separately.
- Memory dedup and recall are semantic. `MEMORY_DEDUP_THRESHOLD` is
  **0.63**, which looks low because dedup compares a *query* embedding
  against stored *document* embeddings — `text-embedding-004` is task-typed
  and those vectors score ~0.24 lower than document-to-document for the
  same pair. Two earlier thresholds (0.82, 0.87) were calibrated on the
  document scale and enforced on the query scale, so dedup could never fire
  at all. `test_the_threshold_still_sits_between_the_bands` in
  `tests/test_memory_live.py` re-measures and fails if that stops holding.
  Sample size is small — four real duplicates, four distinct pairs — so add
  cases when you find them. The failure directions are not symmetric: too
  high leaves duplicates, too low silently eats distinct memories.
- Every memory write and recall now costs an embedding call. Small, but on
  the hot path.
- Memories written *before* semantic memory shipped have no vector, so the
  store's similarity search cannot see them: they still list in the "What
  VITAL knows" panel but are unreachable by recall, and new facts will not
  dedupe against them. `scripts/backfill_memory_vectors.py` re-embeds and
  merges them. On this deployment the nine pre-existing rows were simply
  deleted from the UI instead and allowed to re-accumulate — cheaper than
  arranging production database access for nine rows, four of which were the
  same Albany fact. With real users, run the backfill; dropping their
  memories is not an option.
- Health uploads stream and are memory-safe, but Cloud Run caps HTTP/1.1
  request bodies at 32MB. Larger Apple Health exports need a signed-URL
  upload to GCS; `--use-http2` is **not** a workaround, because uvicorn
  does not speak h2c.
- Without `DATABASE_URL`, graph checkpoints are process-local and do not
  survive restarts.
- Production is designed around a single deployment region.
- Recommendations are informational and are not medical or mental-health
  advice.
