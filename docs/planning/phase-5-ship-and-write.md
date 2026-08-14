# Phase 5 — Ship, Learn, Publish (Week 8)

**Goal:** Real users on the app, feedback loop running, portfolio assets published.

---

## Objectives

- Next.js frontend polished enough for strangers
- 5–10 real users onboarded, feedback instrumented
- 2 technical blog posts + demo video + clean repo
- Iteration backlog from real usage

## 1. Frontend polish (Days 1–2)

Next.js (App Router) on Vercel:

- Chat with per-agent identity ("🌙 Sleep agent" badges from `langgraph_node` metadata in the SSE stream)
- Plan cards with Approve / Edit / Reject → hits the resume endpoint
- Health-data upload with drag-drop + parsing progress
- Memory viewer page ("What VITAL knows about you" + delete buttons) — this page builds more trust than any copy you write
- Auth: Firebase Auth or Clerk (fastest path); user_id flows into thread_id + memory namespaces

## 2. Onboard users (Days 2–4)

- Friends/classmates; NYU folks are ideal (dense city = good activity/event coverage)
- Onboarding conversation (not a form): agent asks 4–5 questions, writes answers straight to memory store — dogfood your own memory pipeline
- Instrument: thumbs up/down per response → LangSmith feedback API; session length; returning-user rate
- Watch 2 users live (screen share). You will find 5 bugs in 20 minutes. Budget for it.

**Expect and log these failure classes:** unexpected phrasings breaking routing, health exports in formats you didn't handle, activity suggestions ignoring budget constraints, memory retrieving stale facts.

## 3. Publish (Days 4–5)

**Blog post 1:** "Building a multi-agent supervisor with LangGraph on Vertex AI" — architecture diagram, routing few-shots, checkpointing, Agent Engine vs Cloud Run comparison from Phase 4.

**Blog post 2:** "Letting an agent write and run code safely: E2B sandboxes + self-repair loops" — the generate→execute→repair subgraph, safety rails, real failure examples.

Where: dev.to or Medium + cross-post to LinkedIn. Include real trace screenshots.

**Demo video (3 min, Loom):** bad-sleep message → sandbox analysis insight → activity suggestion respecting rain → plan approval → calendar events appearing. One continuous take, no slides.

**Repo hygiene:** architecture diagram in README (excalidraw), `docker compose up` for local dev, eval results badge, honest LIMITATIONS.md.

## 4. Resume/interview framing

> Built and shipped a multi-agent wellness copilot (LangGraph, Vertex AI Gemini, E2B sandboxes): supervisor-routed agents with Postgres-checkpointed state, sandboxed self-repairing code execution over user health data, human-in-the-loop plan approval, LLM-as-judge eval gates in CI (>90% routing accuracy), deployed on Cloud Run and Vertex AI Agent Engine serving N users at <$0.05/conversation.

Interview stories this project gives you: debugging routing loops via traces, why topology beats prompts for security, judge-eval flakiness and pass@k, managed vs DIY hosting tradeoffs, memory pollution and retrieval quality.

## 5. What's next (backlog, post-week-8)

- VITAL-user matching (pgvector cosine on interest embeddings, mutual consent)
- Wearable live sync (Terra API / Apple HealthKit) instead of exports
- Proactive agent: scheduled morning briefing ("rain today — your run moves to Thursday; here's an indoor alternative")
- Voice interface; swap in A2A protocol between agents as an experiment
- Multi-city launch = caching + API-quota work (real scaling lessons)

## Definition of done

- [ ] ≥5 users with ≥2 sessions each
- [ ] ≥20 feedback events collected and triaged into issues
- [ ] Both posts published, video recorded, repo public
- [ ] You can explain every box in your architecture diagram without notes
