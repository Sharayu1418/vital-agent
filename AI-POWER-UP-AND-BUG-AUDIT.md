# VITAL — AI Power-Up Plan & Bug Audit

**Date:** 3 Aug 2026
**Scope reviewed:** `vital-app/src/vital/**` (agents, graph, api, storage, security, guardrails, sandbox, tools), `vital-web/app/**` (page, components, lib), build config, deployed shell at `vital-agent.vercel.app`.

**Note on method:** the browser extension wasn't connected, so this is a static audit of the source plus the deployed HTML/build config — not a clicked-through session. Items marked **[verify live]** need a real browser pass to confirm. All 93 frontend tests pass; Python tests couldn't run in this environment (no pytest installed).

**Headline:** the architecture is genuinely strong — topology-as-security around `commit_plan`, fail-closed identity, a deterministic crisis path, inspectable memory. The gap isn't design quality. It's that VITAL is still a *reactive* system: it answers when spoken to, remembers by keyword, and can't act in the world. The five recommendations below are ordered by how much they change that.

---

## Part A — Five ways to harness AI power

### 1. Ship an Energy Forecast engine (predictive, not descriptive) — **top recommendation**

**The gap.** Every agent today looks backward. `sleep_energy` reports debt vs an 8h target; `analyze_sleep_data` runs pandas over history. Nothing predicts. But the product promise — "your energy copilot" — is fundamentally a forecasting promise.

**What to build.** A small per-user model producing a 24–72h energy curve, exposed as one tool (`forecast_energy(horizon_hours)`) that every agent can call.

- **v1 (2–3 days, no ML):** deterministic curve from what you already store — sleep debt accumulation, circadian phase from wake time, the ~3–5h post-wake peak and ~7–9h dip your prompt already hardcodes, plus weekday/weekend bedtime variance. Return `{hour, predicted_energy, confidence, drivers[]}`.
- **v2:** fit per-user coefficients as sleep history accumulates; fall back to population defaults under ~14 nights. Log predicted-vs-reported energy and report calibration.

**Why it's the biggest unlock.** It changes the planner from "here's a reasonable schedule" to "climbing at 2pm Saturday because that's your predicted peak, and I moved the dinner because you'll be in a dip." That rationale is defensible, personal, and impossible for a generic chatbot to produce. It also feeds every other agent for free: Activity Scout filters by predicted energy, Idea Generator matches effort level, People Connector proposes times you'll actually show up for.

**Where it plugs in.** New `src/vital/forecast.py`; register the tool on `sleep_energy` and `planner`; add a 7-day curve to the side panel. `PLANNER_PROMPT` gains one rule: *every item must be justified against the forecast.*

---

### 2. Replace keyword memory with semantic memory + a synthesized profile

**The gap.** `memory.recall()` (memory.py) ranks by raw word-set intersection. Two problems compound:

- Every stored fact begins with `"User "`, and stopwords (`the`, `to`, `a`, `i`) match everything — so on short queries the ranking is close to random.
- No synonym matching. `LIMITATIONS.md` names it: "ceramics" won't retrieve a stored "pottery" fact.

**What to build.**

1. **pgvector retrieval.** `PostgresStore` already supports an `index` config with an embedding function. Wire `text-embedding-004` and switch `recall()` to `store.search(ns, query=..., limit=k)`. This is a ~30-line change behind the existing interface — the cleanest high-leverage fix in the repo.
2. **A synthesized profile document.** Raw facts don't compose. Maintain one rolling ~200-word user summary, regenerated when N new facts land, and inject *that* into agents instead of a semicolon-joined fact list. Cheaper per turn, more coherent, and it gives the "What VITAL knows" panel something a human actually wants to read.
3. **Typed facts.** Add `category` (`location` / `constraint` / `preference` / `dislike` / `relationship`) and `last_confirmed`. Lets you retrieve by kind rather than by string overlap, and lets stale facts decay.

**Bonus:** typed facts make the memory drawer editable instead of only deletable — a real trust feature.

---

### 3. Make VITAL proactive — a scheduled agent loop

**The gap.** VITAL only exists when the tab is open. Every genuinely useful life copilot initiates.

**What to build.** A Cloud Scheduler → Cloud Run job running a `daily_brief` graph per opted-in user:

- Morning: last night's sleep + today's forecast + what's on the committed plan + one concrete adjustment.
- Evening: a 10-second sleep log prompt (the single highest-value data capture you have — manual logging is where personalization dies).
- Event-driven: rain forecast for a committed outdoor item → propose a swap; three short nights in a row → propose a recovery day.

**Delivery:** web push + email first (no new auth surface). Rate-limit hard — one push/day, always dismissible, opt-in default off.

**Why it matters.** It converts a session-based tool into a habit, and it's what makes the memory and forecast investments pay off. It also reuses the HITL topology you already built: a proactive suggestion is just a plan draft awaiting approval.

---

### 4. Let approved plans touch the real world (Google Calendar OAuth)

**The gap.** `LIMITATIONS.md`: "Local table only; nothing writes to Google Calendar yet." A plan that lives only inside VITAL is a plan the user has to re-enter manually — which means most plans die at approval.

**Why VITAL is unusually ready for this.** The hard part of write-actions is consent, and you've already solved it structurally: `commit_plan` has no inbound edge except `request_approval`'s resume (planner.py). Adding Calendar isn't adding risk — it's swapping the `LocalCalendar` implementation behind `make_commit_plan(calendar)`, which is already dependency-injected.

**Build order:**
1. Google Calendar write scope on the existing Firebase Google sign-in (incremental auth, requested only when the user first approves a plan).
2. `GoogleCalendar.commit()` implementing the same `(user_id, plan, plan_hash) -> int` contract. Keep `plan_hash` idempotency — it already prevents double-commits.
3. Read direction next: pull existing busy blocks *into* the planner so it stops proposing conflicts. This is arguably worth more than writing.
4. Then Maps directions between consecutive items — your `PLANNER_PROMPT` promises "realistic transitions (no back-to-back across town)" but has no travel-time data to honor it.

---

### 5. Build the eval + self-critique layer you scaffolded but never wired

**The gap.** `LIMITATIONS.md`: "Evals cover routing well, tool correctness partially, plan quality not yet (LLM-as-judge is scaffolded, not wired)." Plan quality is the actual product, and it's the one thing you can't currently measure or regress-test.

**What to build.**

1. **Plan critic node.** Insert `planner → critic → request_approval`. The critic (Flash, structured output) scores the draft on: every item traceable to something the user said; feasible transitions; sleep target respected; not overpacked. Below threshold → one revision pass, max one loop. This catches most bad plans before a human sees them and costs one cheap call.
2. **LangSmith datasets in CI.** 50 routing cases, 30 plan cases, 20 crisis/near-crisis cases. Run on every PR. You already have `LANGSMITH_TRACING` config — it's unused leverage.
3. **Close the feedback loop.** `/feedback` collects thumbs and writes them to storage, where they sit. Join thumbs-down to the trace, cluster weekly, and you have a prioritized prompt-fix queue instead of guesswork.
4. **Crisis recall test set** — see bug P0-4; this is the eval that matters most.

---

### Cross-cutting: cost and latency

A single user message currently costs, minimum: supervisor call → ReAct agent (≥2 calls) → memory writer call → supervisor again. That's ~4–8 model calls, and the final supervisor hop runs *after* the answer has already streamed — pure added latency before `done`. Three fixes, all small:

- **Skip the trailing supervisor hop** when the agent returned a complete answer and no tool is pending. Saves ~800ms and one call on every single turn.
- **Run the memory writer out-of-band** (background task after the stream closes). It contributes nothing to the user-visible response but sits in the critical path today.
- **Enable Vertex context caching** for the system prompts, which are static and re-sent on every hop.

---

## Part B — Bugs, risks, and things that will bite

Ordered by severity. File references are to the current tree.

### P0 — will cause user-visible failure or unbounded cost

**P0-1. Blocking database calls inside async routes will stall every concurrent user.**
`storage.py` is entirely synchronous (sqlite3, or a sync `psycopg_pool.ConnectionPool`). It is called directly from `async def` handlers in `api.py`: `/threads`, `/threads/{id}`, `/calendar`, `/memories`, `/sleep/recent`, `/feedback`, and all eleven buddy routes — plus `guardrails.budget_exceeded()` at the top of `/chat` and `/approve`, and `record_usage()` *inside the SSE generator*. Each of these blocks the event loop for the full duration of the query. On a single Cloud Run instance, one slow Postgres call freezes every other request, including in-flight SSE streams — which surfaces to users as chat responses that randomly hang mid-sentence. This is the most likely cause of intermittent production weirdness and the hardest to reproduce locally with one user.
**Fix:** wrap storage calls in `starlette.concurrency.run_in_threadpool`, or convert the read-only endpoints to plain `def` (FastAPI auto-threadpools sync handlers).

**P0-2. `/upload/health` buffers the entire upload before checking its size.**
`api.py`: `content = await file.read()` runs *before* `if len(content) > 50MB`. A 300MB file is fully resident in memory before the limit fires, then `ET.fromstring(content)` roughly doubles it. On a default 512MB Cloud Run instance the container is OOM-killed — the user sees a dropped connection, not a 413.
**Fix:** read in chunks with a running counter and abort past the threshold; switch to `ET.iterparse` for the XML path.

**P0-3. The 50MB cap rejects most real Apple Health exports.**
A typical multi-year `export.xml` is 100MB–1GB uncompressed. "Upload your Apple Health data" is a headline feature that will fail for the median user who tries it, and the failure mode (P0-2) is a hang rather than a clear message. Ship the streaming parser and accept zipped uploads before promoting this feature.

**P0-4. The crisis path has narrow recall — the highest-consequence gap in the product.**
`guardrails.CRISIS_PATTERNS` is 17 English substrings. It misses the most common real phrasings ("I don't want to be here anymore", "what's the point", "everyone would be better off"), any non-English message, and typos. It also false-positives on innocuous strings (a message containing "Suicide Squad" triggers the full crisis response and bypasses the agent pipeline). And `/approve` never runs the check at all.
The deterministic-first design is right — keep it. But add a second layer: a fast Flash classifier that runs in parallel and can escalate, with the keyword list as the floor that fires even when the model call fails. Then build the recall test set (see A-5).

**P0-5. The token budget does not actually bound spend.**
`estimate_tokens(str(graph_input)[:2000], "x" * streamed_chars)` counts only the truncated input string and the streamed output. It ignores: full conversation history resent on every hop, all system prompts, tool call payloads and results, the supervisor's calls, the ReAct loop's internal turns, and the memory writer's call. Real consumption is plausibly 10–30× the estimate, so `daily_token_budget = 50_000` permits far more than the "~$0.05/day" the comment claims. Compounding it, the check runs *before* the turn, so one turn can exceed the cap without limit.
**Fix:** use `response_metadata.usage_metadata` from the Vertex responses (accurate and already available), accumulate per-hop, and add a mid-turn abort.

### P1 — degrades experience, likely already happening

**P1-6. Conversation state grows without bound.**
`graph.py::_agent_node` passes the entire `state["messages"]` to the agent on every turn, forever. No trimming, no summarization. Latency and cost rise linearly with thread length, and a long-running thread eventually hits the context window and fails mid-conversation with no recovery path. Users won't know to start a new chat.
**Fix:** `trim_messages` to the last N turns plus a rolling summary, applied in `_agent_node`.

**P1-7. Switching threads mid-stream contaminates the new thread.**
`page.jsx::consume()` guards on identity generation (`guardRef`) but never on `activeId`. Send a message, then click another chat while it's streaming: the old thread's tokens append to the new thread's message list. There's also no `AbortController`, so the fetch keeps running to completion.
**Fix:** capture `activeId` at send time and bail from `sync()` on mismatch; add an `AbortController` and abort on thread switch. This also gives you a stop button, which the UI currently lacks entirely — the composer is disabled for the whole turn with no way out.

**P1-8. Two agents in one turn concatenate into a single bubble with no separator.**
The supervisor re-runs after `memory_writer`; if it doesn't return `FINISH` it routes to another specialist whose tokens append to the same frontend bubble (`stream.js::applyEvent`, `token` case). Up to `MAX_HOPS = 5`. The result reads as one confused answer that changes subject mid-paragraph.
**Fix:** emit an `agent_boundary` SSE event on node change and render separate bubbles.

**P1-9. Concurrent first requests can mint three different anonymous sessions.**
`refreshPanel()` fires `sleepRecent` / `calendar` / `memories` via `Promise.all`. Each resolves identity independently and each can call `_set_session` with a *different* new session id. Last write wins; anything written under the losing ids is orphaned. Masked when `AUTH_REQUIRED=true`, but reachable on first touch and in anon mode.
**Fix:** serialize an identity-establishing call (a cheap `/session` bootstrap) before any parallel fan-out.

**P1-10. `refreshPanel` is all-or-nothing.**
`Promise.all` means one failing endpoint silently blanks the entire side panel — sleep, calendar, and memories all disappear together, with the failure swallowed by `catch { }`. Use `Promise.allSettled`.

**P1-11. Production can silently ship a localhost backend.**
`next.config.mjs`: `NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"`. If the Vercel env var is missing, misspelled, or scoped to the wrong environment, the build succeeds and every user gets "Can't reach the backend. Is it running?" with no signal to you. The committed `.next/required-server-files.json` in this tree does contain `http://localhost:8000`, which is expected for a local build but worth confirming isn't what's deployed. **[verify live]**
**Fix:** throw at build time when `NODE_ENV === "production"` and the var is unset.

**P1-12. CORS admits exactly one origin, so every Vercel preview is broken.**
`api.py`: `allow_origins=[settings().frontend_origin]`. Preview deployments (`vital-agent-git-*.vercel.app`) get no CORS headers, so every branch preview appears completely non-functional — which will cost you real debugging time on a PR someday.
**Fix:** add `allow_origin_regex` for the preview subdomain pattern, keeping the strict single origin for production.

**P1-13. Long analysis turns stream nothing for 30–60 seconds.**
`analyze_sleep_data` → `run_analysis` can run up to 3 repair attempts, each an LLM call plus a fresh E2B microVM boot, all inside a tool call inside a ReAct loop inside a graph node. The tool emits one `on_tool_start` status and then nothing. The user sees `sleep_energy: using analyze_sleep_data` and a frozen screen, and will assume it's broken.
**Fix:** emit progress events per analysis-graph node, and add a client-side elapsed-time hint past ~10s.

**P1-14. Cold starts pay full schema setup on every scale-from-zero.**
The lifespan runs `validate_startup()` (which initializes Firebase Admin), `initialize_storage()` (pool open + `pool.wait(timeout=15)` + DDL), `AsyncPostgresSaver.setup()`, and `PostgresStore.setup()`. With `min-instances=0` on a low-traffic app, most users pay several seconds before their first byte.
**Fix:** `min-instances=1`, and gate the DDL behind a schema-version check rather than running it every boot.

### P2 — worth fixing, lower urgency

**P2-15.** `recall()` scoring counts stopword overlap and every fact starts with `"User "` — ranking is near-random on short queries. Superseded by A-2, but a 5-line stopword filter is a same-day improvement.

**P2-16.** The AST gate bans the attribute `.open` and any `__`-prefixed name. Legitimate generated pandas occasionally trips this, and each rejection burns one of only three repair attempts. Consider narrowing `BANNED_ATTRS` and not counting safety rejections against the repair budget.

**P2-17.** `metrics.log_turn` hashes `user_id` to 10 hex chars. Fine for privacy, but 10 chars is short enough that a determined correlation attack against a known user set is feasible. Use the full digest — it costs nothing.

**P2-18.** Budget resets at midnight UTC, not user-local (already in `LIMITATIONS.md`). For a US user this means the cap resets at 7pm — mid-evening, when they're most likely to be using a "what should I do tonight" app.

**P2-19.** `vital-mobile/` exists in the tree but isn't mentioned in the README and appears unmaintained relative to `vital-web`. Either document its status or archive it — an unexplained third client is confusing to anyone evaluating the repo.

### Needs a live browser pass **[verify live]**

Couldn't be checked statically. Worth 20 minutes with devtools open:

- Console errors and unhandled rejections on first load, signed out and signed in.
- Whether SSE actually streams end-to-end through Cloud Run in production (buffering, and the 60s idle timeout against long analysis turns).
- Firebase auth via the `/__/auth/*` Vercel rewrite to `vital-agent-dev.firebaseapp.com` — popup vs redirect behavior, and Safari/ITP.
- Geolocation permission prompt timing (it fires immediately on reaching `gate === "app"`, which is aggressive for a first-time user).
- Mobile layout at 375px: sidebar, side panel, and the buddy dialog.
- Whether the deployed bundle points at the real Cloud Run URL (P1-11).

---

## Suggested sequence

**Week 1 — stop the bleeding:** P0-1 (blocking DB), P0-2/3 (upload), P0-5 (real token accounting), P1-7 (thread contamination + stop button).

**Week 2 — safety and measurement:** P0-4 (crisis classifier + recall test set), A-5 (evals in CI, plan critic).

**Weeks 3–4 — the differentiator:** A-1 (energy forecast v1) and A-2 (pgvector memory). These two together are what make VITAL feel like it knows you.

**Then:** A-4 (Calendar OAuth), A-3 (proactive loop).

---

*Caveat: this audit is source-based. The live-site items above need browser confirmation before you act on them, and the P0 severity ratings assume production is running with `DATABASE_URL` set and `AUTH_REQUIRED=true`.*
