# Phase 3 — Human-in-the-Loop Planner + People Connector (Week 6)

**Goal:** Planner agent proposes day/week plans the user approves or edits before anything is committed; People Connector surfaces real local groups and events.

---

## Objectives

- `interrupt()`-based approval flow (the canonical LangGraph HITL pattern)
- Google Calendar write tool (gated behind approval)
- People Connector agent with event/community search tools
- Frontend plan-card UI with approve / edit / reject

## Part A — Planner with HITL (Days 1–3)

### 1. Planner node

Synthesizes across agents: sleep plan (from Sleep agent state) + chosen activity + idea time-blocks → one structured plan:

```python
class PlanItem(BaseModel):
    day: str; start: str; end: str
    title: str; kind: Literal["sleep", "activity", "social", "idea_work"]
    rationale: str

class WeekPlan(BaseModel):
    items: list[PlanItem]
    tradeoffs: str   # "moved climbing to Sunday because rain Saturday"
```

### 2. The interrupt

```python
from langgraph.types import interrupt, Command

def request_approval(state: VitalState):
    decision = interrupt({          # graph PAUSES here, state checkpointed
        "type": "plan_approval",
        "plan": state["plan_draft"],
    })
    if decision["action"] == "approve":
        return Command(goto="commit_plan")
    if decision["action"] == "edit":
        return Command(goto="planner",
                       update={"edit_request": decision["feedback"]})
    return Command(goto="supervisor")   # reject
```

Resume from the API when the user clicks a button:

```python
graph.invoke(Command(resume={"action": "approve"}), config=thread_config)
```

Because of Postgres checkpointing, the pause can last seconds or days — the graph resumes exactly where it stopped. This is the pattern behind every "agent asks permission" product flow; know it cold for interviews.

### 3. Calendar tool (gated)

- OAuth per user (Google Calendar API, `calendar.events` scope); store refresh tokens in Secret Manager/KMS-encrypted column
- `commit_plan` node writes events with a `VITAL` tag so they're identifiable/deletable
- Hard rule: this node is reachable ONLY through `request_approval`. Enforce in graph topology (no other edge into it), not in prompts.

## Part B — People Connector (Days 4–5)

### 1. Tools

```python
@tool
def search_events(interest: str, city: str, date_range: str) -> list[dict]:
    """Real local events (Eventbrite/Ticketmaster APIs)."""

@tool
def search_communities(interest: str, city: str) -> list[dict]:
    """Groups: Meetup search, relevant subreddits (Reddit API),
    public Discord directories. Returns name, size, activity_level, link."""
```

### 2. Agent behavior

- Input: an accepted idea/activity from memory ("user saved: learn pottery")
- Output: 2–3 communities + 1–2 upcoming events, each with a one-line "why this fits you" tied to stored profile facts
- Feed accepted communities back into memory → Planner can schedule "Ceramics meetup Thursday"

### 3. v2 note (defer, but design for it)

Opt-in VITAL-user matching: embed interest profiles (pgvector), cosine-match users in same city, mutual-consent intro flow. Schema decision now: keep interests in a dedicated embedded column so matching is a query, not a migration.

## Concepts you learn

`interrupt()`/resume semantics, durable pauses via checkpoints, approval-gated tool execution, graph-topology-as-security, OAuth token handling in agent tools.

## Pitfalls

- **Interrupt payloads must be JSON-serializable** — send the plan dict, not Pydantic objects
- **Double-commit:** user clicks approve twice → make `commit_plan` idempotent (plan hash check)
- **Eventbrite/Meetup API access is restricted these days** — fall back to Places API + curated scraping; don't burn a week fighting API gatekeepers, stub what you must
- **Don't let the LLM decide whether approval is needed.** Topology decides.

## Definition of done

- [ ] Plan proposed → user edits ("move climbing to Sunday") → revised → approved → calendar events appear
- [ ] Rejected plans write nothing anywhere
- [ ] Approval survives a server restart mid-pause
- [ ] People Connector returns ≥3 real, clickable community/event links tied to stored interests
