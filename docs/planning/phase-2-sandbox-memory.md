# Phase 2 — Sandboxed Code Execution + Long-Term Memory (Weeks 4–5)

**Goal:** Sleep agent writes and runs real pandas analysis inside an E2B sandbox on user-uploaded health data. The system remembers users across sessions.

---

## Objectives

- E2B sandbox integration with a self-correcting code-gen loop
- Health data ingestion (Apple Health / Google Fit exports → CSV)
- LangGraph Store for long-term memory (profile learned over time)
- Memory-aware personalization in every agent

## Part A — Sandbox (Week 4)

### 1. E2B setup (Day 1)

```bash
uv add e2b-code-interpreter
# Get API key from e2b.dev — free tier is enough for dev
```

```python
from e2b_code_interpreter import Sandbox

async def run_in_sandbox(code: str, files: dict[str, bytes]) -> dict:
    async with Sandbox() as sbx:
        for name, content in files.items():
            await sbx.files.write(f"/data/{name}", content)
        result = await sbx.run_code(code, timeout=30)
        return {"stdout": result.logs.stdout,
                "error": result.error.traceback if result.error else None,
                "results": [r.text for r in result.results]}
```

### 2. Data ingestion (Day 2)

- Endpoint: `POST /upload/health` accepts Apple Health XML export or Google Fit Takeout → parse sleep records → normalize to CSV (`date, bedtime, wake, duration_min, quality`) → store in GCS bucket per user
- Manual logs from Phase 1 merge into the same table

### 3. Code-gen loop as a subgraph (Days 3–4)

The key pattern — **generate → execute → check → repair**:

```
write_code → execute_sandbox → success? ──yes──▶ interpret_results
                   ▲                │no
                   └── repair_code ◀┘   (max 3 attempts)
```

```python
def repair_code(state):
    return {"code": llm.invoke(
        f"This pandas code failed:\n{state['code']}\n"
        f"Error:\n{state['error']}\nFix it. Return only code."
    ).content, "attempts": state["attempts"] + 1}
```

Analyses to implement as prompted tasks (not hardcoded): sleep debt over 14 days, bedtime consistency (std dev), weekday-vs-weekend social jetlag, correlation of duration → next-day self-reported energy.

### 4. Safety rails (Day 5)

- Static check before execution: reject code containing `os.system`, `subprocess`, network imports (`requests`, `urllib`, `socket`)
- Sandbox has no secrets, no network needed — pass data in, get text out
- 30s timeout, cap attempts at 3, log every executed snippet with user_id

## Part B — Long-term memory (Week 5)

### 1. LangGraph Store (Days 1–2)

```python
from langgraph.store.postgres import PostgresStore
store = PostgresStore.from_conn_string(DB_URL)   # supports embeddings
graph = builder.compile(checkpointer=checkpointer, store=store)
```

Namespaces: `(user_id, "profile")`, `(user_id, "preferences")`, `(user_id, "ideas")`.

### 2. Memory writes (Day 3)

Add a `memory_writer` node after each agent turn — a Flash call that extracts stable facts:

```
"User is 24, lives in Brooklyn, dislikes gyms, loved the pottery
suggestion, sleeps ~1:30am on weekdays."
→ store.put((user_id, "profile"), key, {"fact": ..., "confidence": ...})
```

Rules: only store *stable* facts (not "user is tired today"), update-don't-duplicate (semantic search first, overwrite if similar fact exists).

### 3. Memory reads (Day 4)

Supervisor and every sub-agent prepend retrieved memories:

```python
memories = store.search((user_id, "profile"), query=last_user_msg, limit=5)
```

Effect to demo: "find me something to do" → agent already knows city, budget sensitivity, hates gyms, loves crafts — zero re-asking.

### 4. Memory UI + hygiene (Day 5)

- `GET /memories` endpoint so users can see/delete what's stored (trust + debugging)
- TTL job: facts unused for 90 days get archived

## Concepts you learn

Firecracker microVM sandboxes, code-gen with self-repair loops, prompt-injection-resistant execution boundaries, semantic long-term memory vs thread checkpoints, memory extraction pipelines.

## Pitfalls

- **LLM writes code referencing columns that don't exist** → always inject `df.head()` + `df.dtypes` output into the code-gen prompt
- **Sandbox cold starts feel slow in chat** → stream a "analyzing your sleep data…" status event immediately
- **Memory pollution** — storing trivia makes retrieval worse. Aggressive filtering beats big memory.
- **Never** run generated code on your API server "just for dev." Sandbox from day one.

## Definition of done

- [ ] Upload real sleep export → agent produces a correct, code-backed insight
- [ ] Deliberately corrupt the CSV → repair loop recovers or fails gracefully
- [ ] Second session: agent references facts from first session unprompted
- [ ] Memory viewer endpoint works; executed-code audit log exists
