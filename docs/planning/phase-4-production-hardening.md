# Phase 4 — Production Hardening (Week 7)

**Goal:** Evals in CI, full tracing, cost controls, guardrails — and a second deployment on Vertex AI Agent Engine to compare against Cloud Run.

---

## Objectives

- LangSmith tracing + eval suite running in GitHub Actions
- Deploy the same graph to Vertex AI Agent Engine
- Guardrails: input validation, budgets, loop caps, abuse handling
- Cost + latency dashboards

## 1. Observability (Days 1–2)

```bash
uv add langsmith
# env: LANGSMITH_TRACING=true, LANGSMITH_PROJECT=vital-prod
```

Every trace now shows the full tree: supervisor decision → agent → tool calls → tokens/latency per hop. Tag traces with `user_id`, `phase`, `git_sha`.

Custom metrics to emit (Cloud Monitoring):

- `routing_hops_per_turn` (histogram) — catches loop regressions
- `tokens_per_conversation` by model
- `sandbox_repair_attempts`
- `p95 time-to-first-token`

## 2. Eval suite (Days 2–3)

Three layers, all as LangSmith datasets run in CI:

**a) Routing accuracy (deterministic).** The 20 cases from Phase 1 grown to 50+, including adversarial ("I'm tired of being bored" — sleep or activity?). Assert exact route. Gate: ≥90%.

**b) Tool correctness (deterministic).** Given mocked weather="rain", assert recommendations are indoor. Given a fixed sleep CSV, assert computed sleep debt within tolerance.

**c) Plan quality (LLM-as-judge).** Judge prompt scores 1–5 on: respects stated constraints, specificity (real venues vs generic), consistency with user memory. Use a *different* model as judge (e.g. Claude via API judging Gemini outputs) to avoid self-preference. Gate: mean ≥4.0, no case <3.

```yaml
# .github/workflows/evals.yml — run on PR, block merge on gate failure
- run: uv run pytest tests/evals --langsmith-output
```

Flakiness control: judge runs pass@2 (two judge samples, average).

## 3. Vertex AI Agent Engine deploy (Days 3–4)

```python
from vertexai import agent_engines

app = agent_engines.create(
    agent_engine=graph_builder_fn,     # your LangGraph app factory
    requirements=["langgraph", "langchain-google-vertexai", ...],
    display_name="vital",
)
```

What Agent Engine gives you vs your Cloud Run deploy: managed sessions, built-in tracing, autoscaling, IAM-native auth — at the cost of less control (custom middleware, SSE shaping, cold-start behavior). Run both for a week, write up the comparison (this is the blog post recruiters actually read).

## 4. Guardrails (Day 4)

**Input:**
- Max message length; strip/flag prompt-injection patterns ("ignore previous instructions") — log, don't silently drop
- Off-topic guard: lightweight Flash classifier; VITAL politely declines homework help
- Crisis handling: if messages indicate self-harm/severe distress, respond with support resources and skip normal agent flow. Non-negotiable for a wellness app.

**Execution:**
- Per-user daily token budget (Redis or Postgres counter) → friendly "come back tomorrow" at limit
- Global recursion limit `graph.invoke(..., config={"recursion_limit": 25})`
- Tool allowlist per agent (Sleep agent physically has no calendar tool, etc.)

**Output:**
- Structured-output retry wrapper (1 retry on validation failure, then graceful fallback message)

## 5. Cost & latency (Day 5)

- Prompt caching: system prompts + few-shots are stable → Vertex context caching on the supervisor prompt
- Model tiering audit: every node justifies its model; expect ~80% of calls on Flash
- Target: < $0.05 per average conversation, p95 first token < 2.5s
- Load test: `locust`, 50 concurrent users, watch Cloud SQL connections (add pgbouncer if pool exhausts)

## Concepts you learn

LLM-as-judge evals with gates in CI, trace-driven debugging, prompt caching economics, managed-vs-DIY agent hosting tradeoffs, defense-in-depth guardrails.

## Pitfalls

- **Evals that test the mock, not the agent** — keep a small "live" suite hitting real APIs nightly, separate from PR gates
- **Judge drift:** pin judge model version; re-baseline when upgrading
- **Agent Engine packaging errors** are mostly dependency mismatches — pin exact versions in `requirements`
- **Don't chase 100% routing accuracy** — past ~92%, remaining cases are genuinely ambiguous; handle with clarifying questions instead

## Definition of done

- [ ] PR with a routing regression is auto-blocked by CI
- [ ] Same conversation works on both Cloud Run and Agent Engine deployments
- [ ] Token budget kicks in when exceeded (test it)
- [ ] Dashboard shows cost/conversation and p95 latency
- [ ] Written comparison: Agent Engine vs Cloud Run (500 words, goes in the blog post)
