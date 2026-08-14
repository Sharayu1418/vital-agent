# VITAL

**An agentic life copilot that turns sleep, energy, interests, and intent into grounded actions and plans.**

[![CI](https://github.com/Sharayu1418/vital-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Sharayu1418/vital-agent/actions/workflows/ci.yml)

[Open the web app](https://vital-agent.vercel.app) · [Architecture walkthrough](VITAL-EXPLAINED.md) · [Threat model](SECURITY.md)

### Three things that make this more than a chat wrapper

**Plans cannot be committed without you.** The node that writes to your
calendar has no inbound edge except a human approval resume. It is not a
rule the model follows — it is a path that does not exist, so no prompt
injection can reach it. [`planner.py`](vital-app/src/vital/planner.py)

**The energy forecast is a model, not a prompt.** Borbély's two-process
model — sleep inertia, homeostatic pressure, circadian rhythm, the
afternoon dip — keyed to your own wake time, with constants solved
numerically and pinned by tests. It reports a confidence that degrades
honestly: with no data it says 10% and tells you the curve is not yours.
[`forecast.py`](vital-app/src/vital/forecast.py)

**Quality is measured, not asserted.** Routing, crisis detection, memory
retrieval, grounding and answer quality all have evals with gates. The
answer eval grades the real agent against a rubric and catches the things
unit tests cannot — an answer that is subtly less honest than it claims to
be. [`tests/`](vital-app/tests/)

VITAL is a full-stack, AI-native application built around a supervised team of specialized agents. Instead of sending every request through one oversized prompt, VITAL uses LangGraph to route work to the right specialist, preserve conversational state, pause for human approval, and safely commit approved plans.

The product combines:

- A multi-agent graph powered by LangGraph and LangChain
- Gemini models through Google Vertex AI
- A FastAPI backend with Server-Sent Events (SSE)
- A Next.js and React web experience
- Firebase Authentication with backend token verification
- Persistent memory, sleep data, plans, chats, and activity coordination
- Human-in-the-loop approval before plans become calendar events

> VITAL is a planning and wellness companion, not a medical device or a substitute for professional care.

## Why VITAL Uses Agents

Life-planning requests are rarely one-dimensional. A message such as "I slept badly, but I still want to do something social this weekend" may require sleep context, weather, local places, personal interests, other people, and a realistic schedule.

VITAL separates those responsibilities into focused agents:

| Agent | Responsibility |
| --- | --- |
| Supervisor | Reads the conversation and routes each turn to the best specialist |
| Activity Scout | Combines weather, Google Places, time, budget, and energy to suggest real activities |
| Sleep and Energy | Records sleep, reads recent patterns, and analyzes uploaded Apple Health or CSV data |
| Idea Generator | Turns interests and constraints into concrete, personalized ideas |
| People Connector | Finds opt-in activity buddies, the places an activity actually happens, and public events |
| Planner | Produces structured plans with timing, rationale, and tradeoffs, justified against the energy forecast |

Memory extraction is not an agent. It runs after the answer has already been
streamed, so remembering something never makes anyone wait — it was a graph
node until that cost showed up in the latency numbers.

This architecture keeps each prompt and toolset narrow, makes routing observable, and lets important actions use explicit workflow rules instead of model discretion alone.

## Agent Workflow

```mermaid
flowchart TD
    U["User message"] --> API["FastAPI /chat"]
    API --> S["Supervisor"]
    S --> A["Activity Scout"]
    S --> E["Sleep and Energy"]
    S --> I["Idea Generator"]
    S --> P["People Connector"]
    S --> PL["Planner"]

    A --> END["End of turn"]
    E --> END
    I --> END
    P --> END

    PL --> H["Human approval interrupt"]
    H -->|Approve| C["Commit plan"]
    H -->|Edit| PL
    H -->|Reject| X["End without commit"]

    C --> DB["Calendar and plan storage"]
    END --> R["Stream response over SSE"]

    style H fill:#4a3,color:#fff
    style C fill:#a33,color:#fff
```

Two details in that shape are deliberate.

**Specialists go straight to the end of the turn.** They used to return to
the supervisor via a memory-writing node, on the theory that a second
specialist might be chained. In production that theory cost 5× — the
supervisor could not tell an agent had already answered, so it re-routed
the same message until the hop guard fired. Turns took 37–57 seconds and
12–16k tokens; now 9.6s and 3.3k. Memory extraction moved after the
response, where it costs the user nothing.

**`Commit plan` has exactly one inbound edge**, and it comes from the
human approval resume. That is the security boundary: not a rule in a
prompt, but the absence of any other path.

The graph is stateful and bounded:

- The supervisor uses structured output to choose a route.
- Routing history prevents uncontrolled loops.
- Specialist agents use ReAct-style tool calling.
- Checkpoints allow conversations and approval interrupts to resume.
- The planner cannot reach `commit_plan` without the approval node.
- Production can use Postgres-backed LangGraph checkpoints and storage.

## LangChain and LangGraph

VITAL uses the two libraries for different jobs.

### LangChain

LangChain provides the model and agent building blocks:

- Vertex AI Gemini integration
- Message primitives and prompt composition
- Tool definitions and tool calling
- Structured Pydantic output
- ReAct specialist agents
- Model invocation and response handling

### LangGraph

LangGraph provides the application workflow:

- A typed shared state based on `MessagesState`
- Explicit nodes and conditional edges
- Supervisor-to-specialist routing
- Checkpointed conversations
- Persistent stores for long-term user context
- Human-in-the-loop `interrupt` and resume behavior
- Deterministic topology around plan approval and commit

LangChain helps each agent reason and use tools. LangGraph defines what agents are allowed to do, how they collaborate, and where the user must remain in control.

## Product Experience

### Web Application

The Next.js frontend provides:

- Google sign-in through Firebase Authentication
- Streaming chat, with status lines that say what is happening rather than which function is running
- A thread sidebar with persistent conversation history
- Structured plan cards with approve, edit, and reject controls
- A predicted energy curve for the next 24 hours, shown with its confidence
- Fitbit and Pixel Watch connection, with a visible "Reconnect" state when authorisation expires
- An opt-in morning brief by web push — one notification, or none
- Recent sleep summaries and trend analysis
- Apple Health XML and CSV upload, kept as the fallback it is
- A visible and deletable "What VITAL knows" memory view
- Opt-in activity buddy posts and requests, with a shared meeting-point PDF once a match is accepted
- Voice input and optional read-aloud responses
- Daylight theming computed from the real sunrise and sunset at the user's location
- Responsive layouts for desktop and mobile

The first-use experience is intentionally lightweight: users can begin with a chat instead of completing a long onboarding form.

### API and Agent Runtime

The FastAPI service:

- Verifies Firebase bearer tokens and maps them to stable internal users
- Streams graph events to the browser with SSE
- Runs the LangGraph agent workflow
- Handles human approval and graph resumption
- Stores threads, messages, plans, sleep records, memories, and feedback
- Normalizes health-data uploads
- Provides activity buddy and request APIs
- Applies origin, authentication, and token-budget controls

## Tools and Grounding

Agents can use real data instead of relying only on model memory.

| Capability | Integration |
| --- | --- |
| Language model | Gemini on Google Vertex AI |
| Weather | OpenWeather |
| Local venues | Google Places |
| Public events | Ticketmaster |
| Data analysis | E2B sandbox with Python and pandas |
| Authentication | Firebase Auth and Firebase Admin |
| Observability | Optional LangSmith tracing |
| Persistence | SQLite locally, Postgres in production |

The Sleep and Energy agent can run analysis code in an isolated E2B sandbox. Uploaded health files are normalized before analysis, and sandbox runs are recorded for traceability.

## Memory and Personalization

VITAL distinguishes conversation history from durable memory.

- **Thread history** preserves messages and graph state for a conversation.
- **Long-term memory** stores stable facts such as preferences, recurring constraints, and interests.
- **Domain data** stores sleep records, health imports, ideas, plans, calendar events, and activity posts.

The memory writer uses a confidence threshold and avoids saving transient details. Near-duplicate facts are updated instead of endlessly appended. Users can inspect and delete saved memories from the web interface.

## Human Control and Safety

Human control is part of the graph, not just a sentence in a prompt.

- Plans pause before commit and require explicit approval.
- Editing routes the draft back through the planner.
- Rejecting ends the workflow without creating calendar entries.
- Activity buddy features are opt-in and avoid exposing exact locations or contact details.
- A deterministic crisis-language path bypasses the normal agent flow.
- Per-user token budgets bound model usage.
- Firebase authentication isolates user-owned data in production.

## Architecture

```mermaid
flowchart LR
    B["Next.js web app"] -->|Firebase ID token + SSE| F["FastAPI API"]
    B --> FA["Firebase Auth"]
    F --> G["LangGraph runtime"]
    G --> V["Vertex AI Gemini"]
    G --> T["External tools"]
    G --> E["E2B sandbox"]
    F --> D["SQLite or Postgres"]
    G --> D
    F --> LS["LangSmith, optional"]
```

| Layer | Technology | Purpose |
| --- | --- | --- |
| Web | Next.js 15, React 19 | Authenticated chat, plans, uploads, memory, and activity UI |
| API | FastAPI, Uvicorn, SSE-Starlette | Auth, streaming, uploads, approvals, and product APIs |
| Agent runtime | LangGraph | Routing, state, checkpoints, interrupts, and workflow control |
| Agent components | LangChain | Models, tools, messages, structured output, and ReAct agents |
| Models | Vertex AI Gemini | Reasoning, routing, extraction, and generation |
| Data | SQLite or Postgres | Product records, identities, threads, plans, and health data |
| Analysis | E2B, pandas | Isolated analysis of normalized sleep and health data |
| Identity | Firebase Auth and Admin SDK | OAuth sign-in and backend token verification |
| Deployment | Vercel and Google Cloud Run | Web and API hosting |

## Repository Layout

```text
VITAL/
|-- vital-app/                  # Python API and agent system
|   |-- src/vital/
|   |   |-- agents/             # Specialist agent implementations
|   |   |-- tools/              # Weather, Places, events adapters
|   |   |-- providers/          # Wearable sync seam + Google Health adapter
|   |   |-- api.py              # FastAPI routes and SSE transport
|   |   |-- graph.py            # LangGraph workflow
|   |   |-- supervisor.py       # Structured routing logic
|   |   |-- planner.py          # Structured plans and the approval gate
|   |   |-- forecast.py         # Two-process energy model (pure, no I/O)
|   |   |-- brief.py            # Morning brief composer
|   |   |-- meetup.py           # Fair meeting points for buddy matches
|   |   |-- memory.py           # Semantic memory: store, dedupe, recall
|   |   |-- guardrails.py       # Crisis detection and token budgets
|   |   |-- ratelimit.py        # Per-identity request ceilings
|   |   |-- sandbox.py          # Static gate + microVM for model-written code
|   |   |-- buddies.py          # Activity Buddy Board and moderation
|   |   |-- storage.py          # SQLite and Postgres product storage
|   |   |-- ingest.py           # Apple Health and CSV normalization
|   |   |-- secrets.py          # Encryption for stored OAuth tokens
|   |   |-- oauth_state.py      # CSRF protection for provider linking
|   |   `-- security.py         # Firebase verification and identity
|   |-- tests/                  # 35 files; unit tests, contracts, and evals
|   |-- scripts/                # Threshold tuning, backfill, moderation queue,
|   |                           # feedback digest, post-deploy smoke test
|   `-- pyproject.toml
|-- vital-web/                  # Next.js web application
|   |-- app/
|   |   |-- components/         # Chat, sidebars, plans, buddies, auth
|   |   |-- lib/                # API, auth, location, push, theme helpers
|   |   `-- page.jsx            # Main authenticated application
|   |-- public/sw.js            # Service worker (morning brief only)
|   |-- tests/
|   `-- package.json
|-- docs/                       # Architecture, limitations, observability
|   `-- planning/               # Original design docs and phase plans
`-- .github/workflows/          # CI: backend tests + frontend build
```

## Run Locally

### Prerequisites

- Python 3.11 or newer
- `uv`
- Node.js 20 or newer
- `pnpm` 9
- A Google Cloud project with Vertex AI access
- API keys for the tools you want to enable

### 1. Start the Backend

```bash
cd vital-app
uv sync --extra dev
cp .env.example .env
gcloud auth application-default login
uv run uvicorn vital.api:app --app-dir src --reload
```

The API runs at `http://localhost:8000`.

For local experimentation, the backend can use SQLite and in-memory LangGraph checkpoints. Set `DATABASE_URL` to use Postgres-backed persistence.

### 2. Start the Web App

```bash
cd vital-web
pnpm install
cp .env.example .env.local
pnpm dev
```

Open `http://localhost:3000`.

For local-only anonymous development, use the repository's documented anonymous-mode flag. Production should keep authentication required.

## Configuration

Never commit real credentials. Use `.env` files locally and managed secrets in production.

### Backend

| Variable | Purpose |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | Vertex AI and Firebase project |
| `VERTEX_MODEL` | Gemini model name; defaults to `gemini-2.5-flash` |
| `OPENWEATHER_API_KEY` | Weather grounding |
| `GOOGLE_PLACES_API_KEY` | Venue grounding |
| `TICKETMASTER_API_KEY` | Public event discovery |
| `E2B_API_KEY` | Sandboxed data analysis |
| `DATABASE_URL` | Postgres product data, checkpoints, and stores |
| `AUTH_REQUIRED` | Enforces authenticated API access |
| `FRONTEND_ORIGIN` | Allowed production web origin |
| `LANGSMITH_TRACING` | Enables optional LangSmith tracing |
| `LANGSMITH_API_KEY` | LangSmith authentication |
| `LANGSMITH_PROJECT` | Trace project name |

### Web

| Variable | Purpose |
| --- | --- |
| `NEXT_PUBLIC_API_BASE` | FastAPI base URL |
| `NEXT_PUBLIC_FIREBASE_API_KEY` | Firebase web configuration |
| `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN` | Firebase auth domain |
| `NEXT_PUBLIC_FIREBASE_PROJECT_ID` | Firebase project |
| `NEXT_PUBLIC_FIREBASE_APP_ID` | Firebase web app |
| `NEXT_PUBLIC_ALLOW_ANON` | Local anonymous-development switch |

## Test and Build

### Backend

```bash
cd vital-app
uv run ruff check .
uv run pytest
```

### Web

```bash
cd vital-web
pnpm test
pnpm build
```

Around 480 tests. They cover approval topology, identity and session
handling, tool behaviour and failure contracts, health-data ingestion,
the energy model, moderation, rate limiting, and the CORS contract between
frontend and backend.

CI runs the backend suite and the frontend build on every push. Both of
those existed before CI did, and both would have caught a production
outage that reached users instead — which is why the workflow exists.

### Evals

Four things are measured rather than assumed. They call real models, so
they are opt-in rather than part of CI: a job that goes red for reasons
nobody controls gets ignored, and then it protects nothing.

```bash
VITAL_LIVE_EVALS=1      uv run pytest tests/test_routing.py    # routing, >=90%
CRISIS_LIVE_EVAL=1      uv run pytest tests/test_crisis_live.py # recall/precision
MEMORY_LIVE_EVAL=1      uv run pytest tests/test_memory_live.py # dedupe + recall
ANSWER_QUALITY_EVAL=1   uv run pytest tests/test_answer_quality.py -s
```

The answer eval builds the **real** agent with stubbed tools and grades it
against a rubric. It is the only thing here that can catch an answer being
subtly less useful or less honest than it claims — no unit test sees that.
Its first useful run scored 86%, and four of the seven failures across
three runs turned out to be bugs in the eval rather than the product,
which is worth knowing about model-graded evaluation generally.

A note on reading it: a tool-calling agent is not deterministic even at
temperature 0, so the noise floor is two or three items out of twenty-six.
A sustained drop is a regression; a single case flipping is weather.

## Deployment

The production architecture is designed around:

- **Vercel** for the Next.js web application
- **Google Cloud Run** for the FastAPI and LangGraph service
- **Firebase Authentication** for Google OAuth
- **Postgres** for durable product data and graph state
- **Google Secret Manager** for backend credentials
- **Vertex AI** for Gemini model access
- **LangSmith** as an optional tracing and evaluation layer

The web app sends a Firebase ID token with authenticated API requests. The backend verifies the token, resolves the internal user identity, and scopes data operations to that user.

## Current Limitations

VITAL is an actively developed product. Current technical boundaries include:

- Memory retrieval and dedup are semantic (pgvector via LangGraph's store index); the similarity threshold is validated against real cases in `tests/test_memory_live.py`.
- Approved plans commit to VITAL's relational calendar, not Google Calendar.
- Community discovery has no third-party provider by design — Reddit, Meetup, Facebook Groups, Eventbrite search and Strava clubs have all closed or gone paid since 2019. It runs on Google Places and the Activity Buddy Board instead; see [docs/LIMITATIONS.md](docs/LIMITATIONS.md).
- Conversation history is trimmed to the most recent turns (`HISTORY_LIMIT`) but not *summarised*, so a very long thread loses early context rather than compressing it. Durable facts survive in long-term memory.
- **Apple Watch cannot be synced from a server.** HealthKit data lives on the device and Apple runs no aggregation service, so it needs a native iOS app; aggregators do not avoid this, they hand you an iOS SDK. Fitbit and Pixel Watch sync through the Google Health API. Oura, Whoop and Garmin would each be one adapter file behind `providers/base.py` — deliberately not written, because an integration for a device nobody here owns cannot be verified end to end.
- Wearable OAuth runs in Google's "Testing" publishing status, where refresh tokens expire after 7 days; the panel surfaces this as "Reconnect" rather than failing quietly. Publishing needs OAuth verification plus an annual CASA security assessment.
- Rate limiting is in-process, so across several Cloud Run instances the effective limit is roughly the configured one times the instance count. It stops a single client in a loop; a distributed flood needs Cloud Armor.
- Moderation reports auto-hide a post after three distinct reporters and land in a queue reviewed by script (`scripts/review_reports.py`), not a web console.
- Health uploads stream and are memory-safe, but Cloud Run caps HTTP/1.1 bodies at 32MB; larger Apple Health exports need a signed-URL upload to GCS.
- Without `DATABASE_URL`, graph checkpoints are process-local and do not survive restarts.
- Production is currently designed around a single deployment region.
- Recommendations are informational and are not medical or mental-health advice.

## Design Principles

1. **Route before reasoning.** Send work to the smallest capable specialist.
2. **Ground recommendations.** Prefer live tools and stored user context over generic answers.
3. **Keep users in control.** Approval is required before plans are committed.
4. **Make memory inspectable.** Users should be able to see and delete what the system remembers.
5. **Fail closed around identity.** Authenticated production data must never fall back to a shared user.
6. **Keep onboarding light.** A useful conversation should begin before a long form is necessary.
7. **Use workflow for guarantees.** Safety and commit rules belong in code and graph topology, not only prompts.

---

Built as an exploration of practical multi-agent systems: specialized reasoning, real tools, persistent state, and human control inside one coherent product.
