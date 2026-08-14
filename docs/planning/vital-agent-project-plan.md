# VITAL — A Multi-Agent Life Copilot
### Learning project: LangGraph orchestration + Vertex AI + agent sandboxes, shipped to production

**Goal:** Learn AI agent workflows, orchestration, tool use, and production deployment by building one real app — a personal-energy copilot that replaces "google it yourself" with agents that know you.

---

## 1. The Problem

People juggle 5+ disconnected apps and endless Google searches to manage basic life energy:

- **Sleep:** trackers show data but don't *act* on it (no "your sleep debt is 4h, here's tonight's plan")
- **Pent-up energy:** young people (or any age group) have energy and no idea where to direct it — searching "things to do near me" returns SEO spam, not personalized options
- **Idea generation:** no tool turns your interests + mood + free time into concrete project/activity ideas
- **Finding people:** meeting like-minded people means separately trawling Meetup, Discord, Reddit, Eventbrite
- **Activity discovery:** indoor vs outdoor decisions ignore weather, energy level, budget, and location

**One place. Agents, not search.** You say "I slept badly, I'm restless, I have Saturday free" → the system plans your recovery, finds a matching outdoor activity, and surfaces two local groups doing it.

---

## 2. Why This Is a Great Agent-Orchestration Project

Every hard concept in agentic AI shows up naturally:

| Concept | Where it appears in VITAL |
|---|---|
| Multi-agent orchestration | Supervisor routes between Sleep, Activity, Ideas, People agents |
| Tool calling / induction | Weather, Places, Events, Calendar, health-data tools |
| Stateful workflows | LangGraph checkpointers persist conversation + plan state |
| Long-term memory | User profile (age, interests, sleep patterns) learned over time |
| Human-in-the-loop | Agent proposes a weekly plan → user approves/edits → agent commits |
| Sandboxed execution | Agent writes & runs pandas code on your sleep CSV in an E2B sandbox |
| Production shipping | Deploy to Vertex AI Agent Engine, evals, tracing, cost control |

---

## 3. Agent Architecture

```
                        ┌─────────────────┐
        user ──────────▶│  Supervisor      │  (LangGraph StateGraph,
                        │  (router agent)  │   Gemini 2.x on Vertex AI)
                        └───────┬─────────┘
        ┌──────────┬────────────┼────────────┬──────────────┐
        ▼          ▼            ▼            ▼              ▼
   Sleep &     Activity      Idea         People        Planner
   Energy      Scout         Generator    Connector     (synthesizer)
   Agent       Agent         Agent        Agent
        │          │            │            │
   E2B sandbox  Weather API  memory +    Meetup/Eventbrite
   (analyzes    Places API   interests   Discord/Reddit
   sleep data)  Events APIs  graph       search tools
```

**Agents:**

1. **Supervisor** — classifies intent, routes to sub-agents, merges results. Teaches you LangGraph's supervisor pattern, conditional edges, `Command` handoffs.
2. **Sleep & Energy Agent** — ingests sleep data (Apple Health / Google Fit export, or manual log). Runs Python analysis *inside a sandbox* (sleep debt, circadian drift, energy forecast for the day). Outputs actionable plan, not charts.
3. **Activity Scout** — given energy level + weather + location + budget → ranked indoor/outdoor activities with real links. Tools: Google Places, OpenWeather, Ticketmaster/Eventbrite APIs. This is your "replace Google search" agent.
4. **Idea Generator** — brainstorms projects/hobbies matched to the user's interest profile and available time; stores accepted ideas in memory so it never repeats.
5. **People Connector** — finds local groups, events, Discord servers, subreddits around an accepted idea/activity. (v1: search APIs; v2: opt-in matching between VITAL users.)
6. **Planner** — synthesizes everything into a day/week plan; human-in-the-loop approval before writing to calendar.

---

## 4. Tech Stack

**Agent layer**
- **LangGraph (Python)** — StateGraph, supervisor pattern, checkpointing, interrupts (HITL), `langgraph-checkpoint-postgres`
- **Vertex AI Gemini** (Flash for routing/cheap calls, Pro for planning) — via `langchain-google-vertexai`
- **Structured outputs** with Pydantic for every tool/agent response

**Sandbox**
- **E2B** (managed, Firecracker microVMs, fast cold starts) for agent-generated code execution — analyzing sleep CSVs, computing stats. Alternative: Daytona. Start with E2B free tier.

**Data & memory**
- **Cloud SQL Postgres + pgvector** — user profiles, chat checkpoints, embeddings of interests/ideas (one DB for everything; swap to Vertex AI Vector Search only if scale demands)
- **LangGraph Store** for long-term semantic memory (user preferences learned across sessions)

**External tools (the "induction tools" part)**
- Google Places API, OpenWeather, Eventbrite/Ticketmaster, Google Calendar API, Meetup (scrape/API), Reddit API

**Serving & deploy**
- **FastAPI** backend wrapping the graph, SSE streaming responses
- **Deploy path A (managed, learn it):** Vertex AI Agent Engine — handles autoscaling, sessions, tracing, IAM ([docs](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview))
- **Deploy path B (portfolio credibility, do this too):** Docker → **Cloud Run**, so you can show you understand raw production plumbing
- **Next.js** frontend on Vercel (chat UI + plan cards)

**Production/observability**
- **LangSmith** (or Arize Phoenix, OSS) — tracing every agent hop
- **Evals:** LangSmith datasets + LLM-as-judge on routing accuracy and plan quality; regression suite in CI
- **Guardrails:** input validation, tool-call allowlists, max-iteration caps, per-user token budgets
- GitHub Actions CI/CD, Secret Manager, structured logging → Cloud Logging

---

## 5. Build Plan (8 weeks, phased)

**Phase 0 — Foundations (wk 1)**
Set up GCP project, Vertex AI auth, repo, FastAPI skeleton. Build ONE agent (Activity Scout) as a plain LangGraph ReAct agent with 2 tools (weather + places). Ship it to Cloud Run. *You have a deployed agent in week 1.*

**Phase 1 — Multi-agent core (wk 2–3)**
Supervisor + Sleep + Ideas agents. Postgres checkpointing. Streaming. Learn: conditional edges, subgraphs, handoffs, state schemas.

**Phase 2 — Sandbox + memory (wk 4–5)**
E2B integration: Sleep agent writes pandas code, executes in sandbox, returns insights. LangGraph Store for long-term user memory. Learn: safe code-gen loops, result validation, retry-on-error graphs.

**Phase 3 — HITL + People Connector (wk 6)**
Planner agent with `interrupt()` for plan approval; calendar write tool. People Connector with event/group search.

**Phase 4 — Production hardening (wk 7)**
Deploy to Vertex AI Agent Engine (compare vs your Cloud Run deploy — great blog post material). LangSmith evals: 50-case routing dataset, plan-quality judge. Rate limits, cost dashboard, error budgets.

**Phase 5 — Ship + write (wk 8)**
Onboard 5–10 real users (friends). Fix what breaks. Write 2 blog posts: "Supervisor multi-agent with LangGraph on Vertex AI" and "Sandboxed code execution for agents with E2B." Demo video for portfolio.

---

## 6. Production Problems You'll Actually Hit (and want on your resume)

1. **Routing errors** — supervisor sends "I'm tired" to Activity instead of Sleep → fix with few-shot routing + evals
2. **Latency** — 5-agent chains are slow → parallel fan-out nodes, Flash for cheap hops, streaming partials
3. **Sandbox failures** — generated code crashes → self-correcting loop (error fed back, max 3 retries)
4. **Memory bloat** — checkpoint tables grow fast → TTL policies, summarization nodes
5. **Cost** — token spend per conversation → per-user budgets, prompt caching, model tiering
6. **Nondeterminism in CI** — eval flakiness → judge ensembles, pass@k thresholds
7. **Tool API quotas** — Places/Events rate limits → caching layer, graceful degradation

---

## 7. Success Criteria

- Deployed URL anyone can try; ≥5 real users
- Eval suite in CI with routing accuracy >90%
- Tracing dashboard screenshot-able for portfolio
- 2 published write-ups + clean GitHub repo with architecture diagram
- You can whiteboard supervisor vs swarm vs graph patterns from memory

---

*Stack refs: [Vertex AI Agent Engine + LangGraph tutorial](https://github.com/GoogleCloudPlatform/generative-ai/blob/main/gemini/agent-engine/tutorial_langgraph.ipynb) · [Agent Engine overview](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview) · [Sandbox landscape 2026 (E2B/Daytona/Modal)](https://northflank.com/blog/daytona-vs-e2b-ai-code-execution-sandboxes)*
