"""Guardrails (Phase 4). Three layers, all BEFORE or AROUND the graph:

1. Crisis handling — a wellness app WILL receive messages from people in
   distress. Those bypass the agent pipeline entirely: no routing
   experiment, no tool calls, a direct supportive response. Deterministic
   keyword matching on purpose: a crisis path must not depend on an LLM
   call succeeding.
2. Token budgets — per-user daily cap so one user (or one bug) can't burn
   the project's model budget.
3. Estimation — chars/4 heuristic; good enough for budget enforcement,
   replaced by real usage metadata when LangSmith tracing is on.
"""
from vital import storage
from vital.config import settings

# curated phrases, word-for-word — reviewed, not generated. Deliberately
# conservative: false positives (a caring check-in) are cheap; misses are not.
CRISIS_PATTERNS = [
    "kill myself", "killing myself", "suicide", "suicidal",
    "end my life", "ending my life", "end it all",
    "want to die", "wish i was dead", "wish i were dead",
    "hurt myself", "hurting myself", "harm myself", "self-harm", "self harm",
    "no reason to live", "better off without me", "better off dead",
]

CRISIS_RESPONSE = (
    "Thank you for telling me — what you're feeling matters, and I'm glad "
    "you said something. I'm not the right support for this, but real help "
    "is available right now:\n\n"
    "- 988 Suicide & Crisis Lifeline (US): call or text 988, any time\n"
    "- Crisis Text Line: text HOME to 741741\n"
    "- If you're outside the US, https://findahelpline.com lists local lines\n\n"
    "If you can, please also reach out to someone you trust — a friend, "
    "family member, or counselor. You don't have to carry this alone.\n\n"
    "I'm still here if you want to keep talking."
)


def crisis_check(message: str) -> bool:
    lowered = message.lower()
    return any(pattern in lowered for pattern in CRISIS_PATTERNS)


def estimate_tokens(*texts: str) -> int:
    """Rough chars/4 heuristic.

    FALLBACK ONLY. This badly undercounts a real turn: it sees the request
    string and the streamed reply, but not the conversation history resent
    on every hop, the system prompts, tool call payloads and results, the
    supervisor's own calls, the ReAct loop's internal turns, or the memory
    writer. Measured against provider counts it runs roughly an order of
    magnitude low. Use tokens_from_model_end() wherever the real usage
    metadata is available; this exists for the crisis/short paths and for
    test fakes that report no usage at all.
    """
    return max(1, sum(len(t) for t in texts) // 4)


def tokens_from_model_end(event: dict) -> int:
    """Real token count from an `on_chat_model_end` astream_events payload.

    Counts EVERY model call in a turn — supervisor routing, each ReAct hop
    inside a specialist, the memory writer, the planner — because each one
    emits its own on_chat_model_end. Returns 0 when the provider reports no
    usage metadata (fakes, older integrations), so callers can fall back.

    Shapes handled: AIMessage/AIMessageChunk.usage_metadata (LangChain's
    normalized form), a plain dict, and LLMResult.llm_output.token_usage.
    """
    output = (event.get("data") or {}).get("output")
    if output is None:
        return 0

    usage = getattr(output, "usage_metadata", None)
    if usage is None and isinstance(output, dict):
        usage = output.get("usage_metadata")
    if not usage:
        llm_output = getattr(output, "llm_output", None) or {}
        if isinstance(llm_output, dict):
            usage = llm_output.get("token_usage") or llm_output.get("usage_metadata")
    if not usage or not isinstance(usage, dict):
        return 0

    total = usage.get("total_tokens")
    if total:
        return int(total)
    return (int(usage.get("input_tokens") or 0)
            + int(usage.get("output_tokens") or 0))


def budget_exceeded(user_id: str) -> bool:
    return storage.tokens_used_today(user_id) >= settings().daily_token_budget


def remaining_budget(user_id: str) -> int:
    """Tokens left today, floored at 0. Read once at the top of a turn so the
    stream can abort mid-flight instead of discovering the overrun afterwards
    (the pre-turn check alone let a single turn run unbounded)."""
    return max(0, settings().daily_token_budget - storage.tokens_used_today(user_id))


def record_usage(user_id: str, tokens: int) -> None:
    storage.add_tokens(user_id, tokens)


BUDGET_MESSAGE = ("You've hit today's usage limit — it resets at midnight UTC. "
                  "This keeps VITAL free to run; thanks for understanding.")

OVER_BUDGET_MID_TURN = (
    "I've hit today's usage limit partway through this answer, so I've had to "
    "stop here. It resets at midnight UTC.")
