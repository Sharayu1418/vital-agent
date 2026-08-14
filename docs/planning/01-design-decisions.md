# VITAL — Intent & System Design Decisions

Read this before writing code. Every decision below is written as: **decision → why → tradeoff accepted → what changes at scale.**

---

## Intent

We are building VITAL with three intents, in priority order:

1. **Learning depth over feature count.** Every component exists to teach a production agent-engineering skill (orchestration, sandboxing, HITL, evals). If a feature doesn't teach something, it's cut.
2. **Production-shaped from day one.** Not "prototype now, productionize later." Streaming, typed state, secrets management, and deploys happen in week 1 — because retrofitting them is where most agent projects die.
3. **Scale-aware, not scale-built.** We make decisions that are *cheap now and swappable later*. We do NOT build for 100k users — we build so that getting there is a series of swaps, not a rewrite.

North-star product intent: **replace search-and-assemble with know-me-and-act** for personal energy (sleep, activities, ideas, people).

---

## Core design decisions

### D1. Modular monolith, not microservices

One FastAPI service, one LangGraph graph, agents as subgraphs in one process.

- **Why:** agents share state constantly; network hops between them add latency, serialization bugs, and distributed-tracing pain for zero benefit at our size.
- **Tradeoff accepted:** can't scale/deploy agents independently; one bad agent can degrade the whole service.
- **At scale:** the seams are already there — each agent is a subgraph with a typed interface. If Sleep agent needs GPU-heavy analysis at 50k users, extract it behind the same interface (A2A or plain HTTP). *Design rule: agents may only communicate through state schemas, never import each other's internals.*

### D2. Postgres for everything (checkpoints, memory, vectors, app data)

One Cloud SQL Postgres with pgvector — not Firestore + Redis + Pinecone + Postgres.

- **Why:** one backup story, one connection pool, transactions across chat state and app data, pgvector is plenty below ~1M embeddings.
- **Tradeoff accepted:** Postgres becomes the single bottleneck; vector search slower than dedicated engines at high volume.
- **At scale:** read replicas → pgbouncer → move hot checkpoint reads to Redis (checkpointer interface makes this a config swap) → dedicated vector store (Vertex AI Vector Search) only past ~1M vectors. Each step is independent.

### D3. Stateless API, all state in the checkpointer

Any Cloud Run instance can serve any request; `thread_id` fetches state from Postgres.

- **Why:** this is THE decision that makes horizontal scaling trivial. Cloud Run autoscales 0→N and no instance holds anything.
- **Tradeoff accepted:** every turn pays a checkpoint read/write (~10–30ms). Fine.
- **At scale:** nothing changes. This is why we do it.

### D4. Cloud Run first, Agent Engine second

- **Why:** Cloud Run teaches the raw plumbing (containers, autoscaling, secrets, SSE); Agent Engine teaches the managed path. Running both = informed opinion, portfolio material.
- **Tradeoff accepted:** two deploy targets to maintain for a few weeks.
- **At scale:** pick one. Likely Cloud Run (control over middleware, cold starts, cost) unless Agent Engine's managed sessions/tracing prove worth the lock-in.

### D5. Model tiering: Flash by default, Pro by exception

Supervisor routing, tool-calling agents, memory extraction: **Gemini Flash**. Only final plan synthesis may use Pro — and must justify it in a code comment.

- **Why:** routing is classification; paying Pro prices for classification is the #1 agent cost mistake. ~10x cost difference.
- **Tradeoff accepted:** occasional routing misses that Pro would catch. Fix with few-shots and evals, not bigger models.
- **At scale:** add prompt caching (stable system prompts), batch memory extraction, consider distilling routing to a tiny classifier if volume explodes.

### D6. Tools are thin adapters behind our own interfaces (ports & adapters)

`search_places()` is OUR function with OUR return schema; Google Places is an implementation detail inside it. Same for weather, events.

- **Why:** external APIs change pricing/access constantly (Meetup and Eventbrite already did). The agent prompt-engineering investment lives against OUR schema, not theirs.
- **Tradeoff accepted:** a little wrapper boilerplate.
- **At scale:** swap providers, add caching, or aggregate multiple providers inside the adapter — the agent never notices. This also enables recorded fixtures for deterministic evals.

### D7. SSE over WebSockets

- **Why:** chat is one-directional streaming (server→client); SSE works through Cloud Run out of the box, reconnects natively, no connection-state management.
- **Tradeoff accepted:** no server-push outside an active request (fine until "proactive agent" in the backlog).
- **At scale:** if proactive notifications land, add a separate push channel (FCM) rather than converting chat to WebSockets.

### D8. Sync request/response now, no queues

No Pub/Sub, no Celery. A user turn = one HTTP request that runs the graph.

- **Why:** queues add operational surface we don't need at <1k users; Cloud Run request timeout (60 min max) covers even slow sandbox runs.
- **Tradeoff accepted:** long analyses hold a connection; no retry semantics beyond the client.
- **At scale:** the first real queue use case is health-file ingestion (upload → Pub/Sub → worker parses → notify). Add it then, not before. LangGraph interrupts already give us durable async for approvals without any queue.

### D9. Sandbox: managed E2B, isolate by user, no network

- **Why:** Firecracker isolation without ops burden; free tier covers dev.
- **Tradeoff accepted:** vendor dependency, per-ms pricing at volume, CPU-only.
- **At scale:** sandbox cost grows linearly with analysis usage → cache analysis results keyed on (user, data-hash, question-template); consider self-hosted E2B OSS or Daytona if bills bite. The `run_in_sandbox()` interface is ours (see D6) — swappable.

### D10. Single region (us-east1), no multi-region

- **Why:** users are friends in NYC. Multi-region doubles complexity for zero current benefit.
- **Tradeoff accepted:** latency for a hypothetical SF user; regional outage = downtime.
- **At scale:** Cloud Run multi-region behind a global LB is straightforward; Postgres multi-region is not — that's the real decision, defer it as long as possible.

### D11. Security boundaries live in graph topology, not prompts

Calendar writes reachable only through the approval node; Sleep agent physically has no calendar tool; sandbox has no secrets/network.

- **Why:** prompts are suggestions; topology is enforcement. Prompt injection can't call a tool that isn't wired in.
- **Tradeoff accepted:** less "agentic magic," more explicit graph edges. Good.
- **At scale:** this principle is what lets you pass a security review. Never trade it away.

### D12. Evals as the contract, from Phase 1

The 20-case routing test isn't a nice-to-have — it's the regression contract that makes refactors (D1→extraction, D5→model swaps) safe.

- **Why:** in agent systems, evals play the role types play in normal code. Without them every change is vibes.
- **Tradeoff accepted:** eval maintenance time (~10% of dev time). Cheapest insurance available.

---

## Scaling map (what breaks first, in order)

| Users | First bottleneck | The already-designed fix |
|---|---|---|
| ~100 | External API quotas (Places/weather) | Cache layer inside tool adapters (D6) |
| ~1k | Postgres connections from autoscaled Cloud Run | pgbouncer; cap max instances |
| ~5k | LLM cost curve | Prompt caching, tiering audit, routing distillation (D5) |
| ~20k | Checkpoint table bloat, slow thread loads | TTL + summarization nodes; hot state to Redis (D2) |
| ~50k | One agent dominating latency/cost | Extract that subgraph to its own service (D1) |
| ~100k+ | Vector search, multi-region | Vertex Vector Search; global LB (D2, D10) |

The point of this table in interviews: **you know what you deliberately didn't build, and exactly when you'd build it.**

---

## Decisions we are explicitly deferring

Queues (D8), Redis, multi-region (D10), user-to-user matching infra, wearable live-sync, WebSockets, self-hosted sandboxes, agent-to-agent protocols (A2A). Each has a trigger condition above — before that trigger, building it is procrastination disguised as engineering.
