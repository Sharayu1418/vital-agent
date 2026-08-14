# Phase 1 — Multi-Agent Core (Weeks 2–3)

**Goal:** Supervisor graph routing between Activity Scout, Sleep & Energy, and Idea Generator agents, with Postgres-backed conversation state.

---

## Objectives

- Supervisor pattern with LangGraph `StateGraph`
- Two new agents: Sleep & Energy (v1, no sandbox yet), Idea Generator
- Shared state schema + Postgres checkpointing (conversations survive restarts)
- Streaming through the full graph

## Architecture

```
START → supervisor ──(route)──▶ activity_scout ──▶ supervisor
                   ├──────────▶ sleep_energy  ──▶ supervisor
                   ├──────────▶ idea_generator──▶ supervisor
                   └──(done)──▶ END
```

## Step-by-step

### 1. State schema (Week 2, Day 1)

```python
from typing import Annotated, Literal
from langgraph.graph import MessagesState

class VitalState(MessagesState):
    user_profile: dict          # age_group, city, interests, energy_level
    active_agent: str | None
    plan_draft: dict | None
    routing_history: list[str]  # for debugging loops
```

### 2. Supervisor node (Week 2, Days 1–2)

Use structured output for routing — never parse free text:

```python
class Route(BaseModel):
    next: Literal["activity_scout", "sleep_energy", "idea_generator", "FINISH"]
    reasoning: str  # forces the model to think; also great for debugging

def supervisor(state: VitalState) -> Command:
    decision = llm.with_structured_output(Route).invoke([
        SystemMessage(ROUTER_PROMPT), *state["messages"]
    ])
    if decision.next == "FINISH":
        return Command(goto=END)
    return Command(goto=decision.next,
                   update={"routing_history": state["routing_history"] + [decision.next]})
```

ROUTER_PROMPT needs few-shot examples — this is where most quality lives:

```
"I'm exhausted lately" → sleep_energy
"bored, what should I do this weekend" → activity_scout
"I have energy but no direction/purpose" → idea_generator
"thanks, that's all" → FINISH
```

### 3. Sleep & Energy agent v1 (Week 2, Days 3–4)

Manual data only for now (sandbox comes in Phase 2):

- Tool: `log_sleep(bedtime, wake_time, quality_1_5)` → writes to Postgres
- Tool: `get_sleep_history(days: int)` → reads back
- Prompt: compute sleep debt, give tonight's target bedtime, forecast today's energy peaks (roughly: peak ~3–5h and dip ~7–9h after wake). Actionable output, not lectures.

### 4. Idea Generator agent (Week 2, Day 5)

- Tool: `get_user_interests()` / `save_idea(idea, category)`
- Generates 3 ideas scored on: matches interests, fits available time, novelty (check saved ideas to avoid repeats)

### 5. Wire the graph (Week 3, Days 1–2)

```python
builder = StateGraph(VitalState)
builder.add_node("supervisor", supervisor)
builder.add_node("activity_scout", activity_scout_node)   # subgraph
builder.add_node("sleep_energy", sleep_energy_node)
builder.add_node("idea_generator", idea_generator_node)
builder.add_edge(START, "supervisor")
# sub-agents return to supervisor via Command(goto="supervisor")
```

Each sub-agent is its own compiled graph invoked inside a node function — this keeps their internal tool loops out of the parent state.

### 6. Postgres checkpointing (Week 3, Day 3)

```bash
uv add langgraph-checkpoint-postgres psycopg[binary,pool]
gcloud sql instances create vital-db --database-version=POSTGRES_16 \
  --tier=db-f1-micro --region=us-east1
```

```python
from langgraph.checkpoint.postgres import PostgresSaver
graph = builder.compile(checkpointer=PostgresSaver.from_conn_string(DB_URL))
# invoke with config={"configurable": {"thread_id": user_id + session_id}}
```

Now a user can close the tab, come back, and continue the conversation.

### 7. Graph-level streaming (Week 3, Day 4)

Stream `on_chat_model_stream` events but tag which agent is talking (`event["metadata"]["langgraph_node"]`) so the frontend can show "Sleep agent is thinking…".

### 8. Test routing (Week 3, Day 5)

Write 20 test utterances → expected route as a pytest parametrized test. This becomes your Phase 4 eval seed.

## Concepts you learn

Supervisor pattern, `Command` handoffs, subgraphs, custom state schemas, reducers, checkpointing/threads, structured-output routing.

## Pitfalls

- **Infinite supervisor loops:** agent returns to supervisor, supervisor re-routes to same agent. Cap with `routing_history` length check → force FINISH after 5 hops.
- **State bloat:** don't dump full tool results into `messages`; summarize into state fields.
- **Cloud SQL from Cloud Run:** use the Cloud SQL Python connector or Unix socket, not public IP.
- **Model tiering:** supervisor = Flash (it's a classifier); sub-agents = Flash; only the final synthesis needs Pro, if at all.

## Definition of done

- [ ] One conversation touches 2+ agents with correct routing
- [ ] Kill the server mid-conversation, restart, thread resumes
- [ ] 20-case routing test ≥ 18 passing
- [ ] Deployed update on Cloud Run
