# VITAL — A Multi-Agent Life Copilot
### Learning project: LangGraph orchestration + Vertex AI + agent sandboxes, shipped to production

**Goal:** Learn AI agent workflows, orchestration, tool use, and production deployment by building one real app — a personal-energy copilot that replaces "google it yourself" with agents that know you.

**Phase files:** [Phase 0](phase-0-foundations.md) · [Phase 1](phase-1-multi-agent-core.md) · [Phase 2](phase-2-sandbox-memory.md) · [Phase 3](phase-3-hitl-people-connector.md) · [Phase 4](phase-4-production-hardening.md) · [Phase 5](phase-5-ship-and-write.md)

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
2. **Sleep & Energy Agent** — ingests sleep data (Apple Health / Google Fit export, or manual log). Runs Python analysis *inside a sandbox* (sleep debt, circadian drift, energy forecast). Outputs actionable plan, not charts.
3. **Activity Scout** — given energy level + weather + location + budget → ranked indoor/outdoor activities with real links. Tools: Google Places, OpenWeather, Ticketmaster/Eventbrite APIs. Your "replace Google search" agent.
4. **Idea Generator** — brainstorms projects/hobbies matched to interest profile and available time; stores accepted ideas in memory so it never repeats.
5. **People Connector** — finds local groups, events, Discord servers, subreddits around an accepted idea/activity. (v1: search APIs; v2: opt-in matching between VITAL users.)
6. **Planner** — synthesizes everything into a day/week plan; human-in-the-loop approval before writing to calendar.

---

## 4. Tech Stack

**Agent layer**
- **LangGraph (Python)** — StateGraph, supervisor pattern, checkpointing, interrupts (HITL), `langgraph-checkpoint-postgres`
- **Vertex AI Gemini** (Flash for routing/cheap calls, Pro for planning) — via `langchain-google-vertexai`
- **Structured outputs** with Pydantic for every tool/agent response

**Sandbox**
- **E2B** (managed, Firecracker microVMs, fast cold starts) for agent-generated code execution. Alternative: Daytona. Start with E2B free tier.

**Data & memory**
- **Cloud SQL Postgres + pgvector** — user profiles, chat checkpoints, embeddings of interests/ideas
- **LangGraph Store** for long-term semantic memory

**External tools**
- Google Places API, OpenWeather, Eventbrite/Ticketmaster, Google Calendar API, Meetup, Reddit API

**Serving & deploy**
- **FastAPI** backend wrapping the graph, SSE streaming
- **Deploy path A:** Vertex AI Agent Engine — autoscaling, sessions, tracing, IAM ([docs](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview))
- **Deploy path B:** Docker → **Cloud Run** (portfolio credibility — raw production plumbing)
- **Next.js** frontend on Vercel

**Production/observability**
- **LangSmith** (or Arize Phoenix) — tracing every agent hop
- **Evals:** LangSmith datasets + LLM-as-judge; regression suite in CI
- **Guardrails:** input validation, tool-call allowlists, max-iteration caps, per-user token budgets
- GitHub Actions CI/CD, Secret Manager, Cloud Logging

---

## 5. Build Plan (8 weeks)

| Phase | Weeks | Outcome |
|---|---|---|
| [0 — Foundations](phase-0-foundations.md) | 1 | Activity Scout agent live on Cloud Run |
| [1 — Multi-agent core](phase-1-multi-agent-core.md) | 2–3 | Supervisor + 3 agents, Postgres checkpointing |
| [2 — Sandbox + memory](phase-2-sandbox-memory.md) | 4–5 | E2B code execution, long-term memory |
| [3 — HITL + People](phase-3-hitl-people-connector.md) | 6 | Plan approval flow, community search |
| [4 — Production hardening](phase-4-production-hardening.md) | 7 | Evals in CI, Agent Engine deploy, guardrails |
| [5 — Ship + write](phase-5-ship-and-write.md) | 8 | Real users, blog posts, demo video |

---

## 6. Production Problems You'll Actually Hit

1. **Routing errors** — supervisor sends "I'm tired" to Activity instead of Sleep → few-shot routing + evals
2. **Latency** — 5-agent chains are slow → parallel fan-out, Flash for cheap hops, streaming partials
3. **Sandbox failures** — generated code crashes → self-correcting loop (max 3 retries)
4. **Memory bloat** — checkpoint tables grow fast → TTL policies, summarization nodes
5. **Cost** — per-user budgets, prompt caching, model tiering
6. **Nondeterminism in CI** — judge ensembles, pass@k thresholds
7. **Tool API quotas** — caching layer, graceful degradation

---

## 7. Success Criteria

- Deployed URL anyone can try; ≥5 real users
- Eval suite in CI with routing accuracy >90%
- Tracing dashboard screenshot-able for portfolio
- 2 published write-ups + clean GitHub repo with architecture diagram
- You can whiteboard supervisor vs swarm vs graph patterns from memory

---

*Refs: [Agent Engine + LangGraph tutorial](https://github.com/GoogleCloudPlatform/generative-ai/blob/main/gemini/agent-engine/tutorial_langgraph.ipynb) · [Agent Engine overview](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview) · [Sandbox landscape 2026](https://northflank.com/blog/daytona-vs-e2b-ai-code-execution-sandboxes)*
