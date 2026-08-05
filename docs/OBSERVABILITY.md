# Observability

VITAL emits structured JSON to stdout. Cloud Run forwards it to Cloud
Logging, which parses it into `jsonPayload` — so log-based metrics and
alerts need no metrics client library.

Two metric kinds today: `chat_turn` and `tool_call`.

---

## Why tool health exists

The Reddit community-search integration failed on **every single call** for
months. It failed *gracefully* — returning an `error` key, which the agent
faithfully reported as "community search is temporarily down" — so a
permanently dead feature was indistinguishable from a transient blip.
Nobody noticed until someone read the code.

The lesson generalises: **graceful degradation without a failure rate hides
outages indefinitely.** Every remaining tool can fail the same silent way —
Places on a quota problem, E2B on an expired key, Ticketmaster on a
deprecation. The alert below is what turns "the agent apologised again"
into "search_places has been failing for an hour."

---

## `tool_call`

One line per tool invocation, emitted centrally from `_graph_stream` in
`api.py` — not from inside each tool, so a newly added tool is covered the
day it lands and nobody has to remember to instrument it.

```json
{"metric": "tool_call", "tool": "search_places", "outcome": "error",
 "error": "venue search unavailable (HTTPStatusError)",
 "duration_ms": 1243, "user": "bb82030dbc"}
```

| Field | Meaning |
| --- | --- |
| `tool` | Tool name. **Group by this** — one dead tool is invisible in an overall rate |
| `outcome` | `ok` or `error`. Derived from the D6 contract: a dict with a non-empty `error` key |
| `error` | Truncated failure detail, usually the exception class |
| `duration_ms` | Wall time for the call |
| `user` | Hashed. Raw identity never reaches logs — anonymous session ids are identity too |

`no_data` is deliberately **not** an error. When `analyze_sleep_data`
reports the user hasn't uploaded anything, nothing is broken; counting it
would inflate the rate we alert on.

### Create the log-based metric

```bash
gcloud logging metrics create tool_errors \
  --project vital-agent-dev \
  --description "Tool calls that returned an error, by tool" \
  --log-filter='resource.type="cloud_run_revision"
    resource.labels.service_name="vital-api"
    jsonPayload.metric="tool_call"
    jsonPayload.outcome="error"'
```

And a companion for the denominator, so you can alert on a *ratio* rather
than raw counts (raw counts scale with traffic and will page you at 9am for
no reason):

```bash
gcloud logging metrics create tool_calls_total \
  --project vital-agent-dev \
  --description "All tool calls, by tool" \
  --log-filter='resource.type="cloud_run_revision"
    resource.labels.service_name="vital-api"
    jsonPayload.metric="tool_call"'
```

> Add `tool` as a metric label in **Logging → Log-based Metrics → Edit →
> Labels** (`jsonPayload.tool`, type STRING). Without it every tool is
> pooled and a single dead integration stays hidden — which is the exact
> failure this is meant to catch.

### The alert that would have caught Reddit

**Condition:** `tool_errors` grouped by `tool`, **> 90% of `tool_calls_total`
for that tool, sustained 30 minutes.**

A permanently broken integration sits at 100% and trips within the hour. A
flaky provider having a bad afternoon sits at 20–40% and doesn't page you.

Console: Monitoring → Alerting → Create Policy → metric `tool_errors` →
group by `tool` → condition "percent of `tool_calls_total`" → threshold 90%
→ duration 30m.

### Worth adding once you have traffic

- **`duration_ms` p95 per tool** > 8s — `tool_timeout_seconds` is 8, so
  sustained p95 near it means users are watching a spinner for a tool that
  is about to give up anyway.
- **Zero calls for a tool over 24h** — a tool the router never picks is
  either dead weight or a routing bug. Neither is visible from error rates.

---

## `chat_turn`

One line per completed turn.

```json
{"metric": "chat_turn", "user": "...", "thread": "...", "routing_hops": 1,
 "est_tokens": 3302, "duration_ms": 9612, "heuristic_tokens": 68,
 "undercount_ratio": 48.6, "routes": ["activity_scout"]}
```

| Field | Watch for |
| --- | --- |
| `routing_hops` | **Should be 1.** It sat pinned at `MAX_HOPS` in production — the supervisor re-routing the same message until the guard stopped it, costing 5x in latency and tokens |
| `routes` | *Which* agents ran. A count of 5 hid that it was the same specialist five times; the list says so immediately |
| `est_tokens` | Real provider usage, summed across every model call in the turn |
| `heuristic_tokens` / `undercount_ratio` | The old chars/4 estimate and how far off it was. Measured at 139–182x before real accounting landed |
| `duration_ms` | p95. Was 37–57s, now ~9.6s warm |

**Useful queries**

```bash
# recent turns
gcloud logging read 'jsonPayload.metric="chat_turn"' --limit 20 \
  --project vital-agent-dev \
  --format="table(jsonPayload.duration_ms, jsonPayload.routing_hops,
                  jsonPayload.routes, jsonPayload.est_tokens)"

# tool failures in the last hour
gcloud logging read 'jsonPayload.metric="tool_call"
  jsonPayload.outcome="error"' --freshness=1h \
  --project vital-agent-dev \
  --format="table(jsonPayload.tool, jsonPayload.error)"
```

### Other kinds on the same `chat_turn` metric

`crisis_response` (the screen fired and the graph was bypassed),
`budget_abort` (a turn stopped mid-flight on the token cap), and
`feedback_up` / `feedback_down`.

Worth watching `crisis_response` volume — a sudden change means either the
classifier drifted or something real is happening with your users.
