# Blog post outlines (Phase 5 deliverable — write after real users)

## Post 1: "A supervisor multi-agent system on LangGraph + Vertex AI, with receipts"

Hook: everyone posts agent demos; almost nobody posts the eval numbers and
the routing bugs. This is the version with both.

1. The product in one paragraph + architecture diagram (supervisor ⇄ 5 agents)
2. Routing is classification: structured output + few-shots on Flash,
   NOT a bigger model. Show ROUTER_PROMPT evolution and the eval table
   (26 cases, accuracy before/after each few-shot change).
3. State as the only interface between agents (why subgraph extraction
   stays cheap). Postgres checkpointing + the kill-server-mid-conversation demo.
4. Security lives in topology: the calendar-commit node has one inbound
   edge and it's behind a human interrupt. Prompt injection can't call a
   tool that isn't wired in. (Screenshot the graph.)
5. Cloud Run vs Agent Engine: the same graph both ways — what the managed
   path gives (sessions, tracing, IAM) and what it costs you (your
   middleware: auth, budgets, crisis path all live OUTSIDE the engine).
6. Numbers: routing accuracy, p95 first-token, $/conversation, hop histogram.

## Post 2: "Letting an agent write and run code safely: E2B + a self-repair loop"

Hook: the sleep agent writes pandas against your health data. Here's the
generate → gate → execute → repair subgraph and every way it failed.

1. Why sandbox at all (health data + generated code = worst case pairing)
2. The AST gate: banned imports/calls/dunders/URL literals — and its honest
   scope (defense-in-depth; the microVM protects the host; network egress
   is its own control). Include the bypass list a reviewer found and the
   fixes — that's the most valuable section.
3. The repair loop: feeding tracebacks + real column previews back; why
   max_attempts=3; audit-logging every executed snippet.
4. Real failure gallery: hallucinated columns, fence-wrapped code, the
   model importing `os` for no reason, corrupted CSVs.
5. Cost/latency: caching analysis by (data-hash, question) — numbers.

Cross-post: dev.to + LinkedIn. Every claim gets a trace screenshot or an
eval table. No claim without a receipt.
