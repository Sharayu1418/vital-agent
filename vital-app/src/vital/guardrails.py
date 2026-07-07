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
    """Rough: ~4 chars/token for English. Floor of 1 to always record use."""
    return max(1, sum(len(t) for t in texts) // 4)


def budget_exceeded(user_id: str) -> bool:
    return storage.tokens_used_today(user_id) >= settings().daily_token_budget


def record_usage(user_id: str, tokens: int) -> None:
    storage.add_tokens(user_id, tokens)


BUDGET_MESSAGE = ("You've hit today's usage limit — it resets at midnight UTC. "
                  "This keeps VITAL free to run; thanks for understanding.")
