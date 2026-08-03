"""Guardrails (Phase 4). Three layers, all BEFORE or AROUND the graph:

1. Crisis handling — a wellness app WILL receive messages from people in
   distress. Those bypass the agent pipeline entirely: no routing
   experiment, no tool calls, a direct supportive response.
2. Token budgets — per-user daily cap so one user (or one bug) can't burn
   the project's model budget.
3. Estimation — chars/4 heuristic; good enough for budget enforcement,
   replaced by real usage metadata when LangSmith tracing is on.

Crisis detection (P0-4)
-----------------------
The original detector was a substring match against ~18 English phrases.
Measured against tests/crisis_cases.py it caught 13% of real distressed
phrasings and fired on 4 innocent ones. Both halves of that are bad: it
missed almost everything indirect ("I don't want to be here anymore"),
everything non-English, and every typo — while interrupting people who
mentioned a film called Suicide Squad.

Substring matching cannot fix this. Paraphrase, other languages and slang
are exactly what a language model is for, so:

    assess()  ->  classifier decides (sees the last few messages for
                  context, because "I don't want to be here anymore" means
                  nothing in isolation)
              ->  on error/timeout, fall back to DETERMINISTIC matching

The deterministic layer did not go away; it changed job. It is now the
outage floor, not the detector. If the model call fails we match against
the strict list OR the broad concern net and fail SAFE — during an outage
we would rather send a caring, unnecessary message than miss someone. That
guarantees the crisis path never depends on an LLM call succeeding, which
was right in the original design, while letting the normal path be far
more accurate than keywords can be.

Cost: one small model call per message, before the graph starts. Roughly
150 tokens and ~250ms against a turn that already spends several seconds
across 4-8 calls. Running it concurrently with the graph and gating the
stream would remove even that; deliberately not done here, because a
safety path that reads top-to-bottom is worth more than 250ms.
"""
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Sequence

from pydantic import BaseModel, Field

from vital import storage
from vital.config import settings

# how many prior messages the classifier sees alongside the current one
CONTEXT_TURNS = 4

# Shared pool for the timed-out classifier call. Bounded on purpose: if the
# provider hangs, workers fill up, later submissions never start, and their
# futures time out into the deterministic fallback. Degraded but safe, and
# it cannot spawn a thread per request.
_CLASSIFIER_POOL = ThreadPoolExecutor(max_workers=8,
                                      thread_name_prefix="crisis-screen")

# Strict list: curated phrases, word-for-word — reviewed, not generated.
# OUTAGE FLOOR ONLY (see module docstring). Kept exactly as it was so that
# a classifier outage degrades to the old behaviour and never worse.
CRISIS_PATTERNS = [
    "kill myself", "killing myself", "suicide", "suicidal",
    "end my life", "ending my life", "end it all",
    "want to die", "wish i was dead", "wish i were dead",
    "hurt myself", "hurting myself", "harm myself", "self-harm", "self harm",
    "no reason to live", "better off without me", "better off dead",
]

# Broad net: high recall, deliberately low precision. Only consulted when
# the classifier is unavailable, where over-firing is the safe direction.
# Includes slang, common misspellings, and the phrasings that a
# English-only list cannot see at all.
CONCERN_PATTERNS = [
    # indirect / passive ideation
    "don't want to be here", "dont want to be here",
    "don't want to wake up", "dont want to wake up",
    "don't want to live", "dont want to live", "dont wanna be alive",
    "don't want to be alive", "tired of being alive", "tired of living",
    "want it all to stop", "want it to stop", "make it stop",
    "not existing", "stop existing", "disappear forever",
    "notice if i was gone", "notice if i were gone", "if i was gone",
    "without me in it", "no way out", "don't see a way out",
    "can't keep going", "cant keep going", "can't go on", "cant go on",
    "done with everything", "ending things", "end things",
    "what's the point", "whats the point", "no point in anything",
    # plan or means
    "how i'd do it", "how i would do it", "planning how",
    "pills ready", "ways to go", "off the road",
    "wrote letters", "written letters", "goodbye letter", "note for my family",
    # self-harm
    "cutting again", "cut myself", "burned myself", "burnt myself",
    "hurt myself", "harm myself",
    # slang / euphemism / misspelling
    "kms", "unalive", "off myself", "killmyself", "kill myself",
    "end my life", "take my own life", "took my own life",
    # non-English (the substring list is blind to all of these)
    "no quiero vivir", "acabar con todo", "quitarme la vida",
    "ne veux plus vivre", "en finir avec la vie",
    "nicht mehr leben", "das leben nehmen",
    "non voglio più vivere", "non voglio piu vivere",
    "não quero mais viver", "nao quero mais viver",
    "jeena nahi", "marna chahta", "marna chahti",
    "不想活", "活不下去", "生きていたくない", "死にたい",
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
    """Strict deterministic match. No longer the detector — see assess()."""
    lowered = message.lower()
    return any(pattern in lowered for pattern in CRISIS_PATTERNS)


def concern_signal(message: str) -> bool:
    """Broad deterministic match: high recall, low precision."""
    lowered = message.lower()
    return any(pattern in lowered for pattern in CONCERN_PATTERNS)


def deterministic_crisis(message: str) -> bool:
    """The outage floor: strict list OR broad net. Used only when the
    classifier cannot be reached, where firing too often beats missing."""
    return crisis_check(message) or concern_signal(message)


CLASSIFIER_PROMPT = """You screen messages sent to a sleep and energy \
wellbeing assistant. Decide whether THIS user is personally at risk right now.

Answer "crisis" when the user expresses, about themselves:
- wanting to die, not wanting to exist, or not wanting to be here
- suicidal thoughts, intent, or a plan
- a method, means, or preparation (letters, pills, timing)
- current or recent self-harm
- hopelessness that reads as "I cannot keep going"

Answer "clear" for everything else, including:
- idiom and hyperbole ("dead tired", "this deadline is murder", "dying to
  try that class", "killing it at work")
- films, books, news, coursework or research that mention suicide or
  self-harm as a topic
- ordinary low mood, exhaustion, burnout, stress, boredom or a bad week
  with no reference to dying or self-harm. This assistant is FOR tiredness
  and low energy; those are its normal subject matter, not a crisis.
- concern about somebody else rather than the user

Judge the user's meaning, not the presence of any particular word. Messages
may be in any language, may be misspelled, and may use slang ("kms",
"unalive"). If the user's own safety is genuinely uncertain, answer "crisis".

Recent conversation (for context; judge the LAST user message):
{context}"""


class RiskVerdict(BaseModel):
    reasoning: str = Field(description="One short sentence.")
    risk: Literal["crisis", "clear"]


def _default_classifier(context: str) -> str:
    """Real classifier. Lazy import so tests and local dev stay offline."""
    from langchain_core.messages import SystemMessage
    from langchain_google_vertexai import ChatVertexAI

    cfg = settings()
    llm = ChatVertexAI(model=cfg.vital_model, temperature=0.0,
                       project=cfg.google_cloud_project,
                       location=cfg.google_cloud_location)
    verdict = llm.with_structured_output(RiskVerdict).invoke(
        [SystemMessage(content=CLASSIFIER_PROMPT.format(context=context))])
    return verdict.risk


def build_context(message: str, history: Sequence[str] = ()) -> str:
    """Last few turns plus the message being judged. Context matters: 'I
    don't want to be here anymore' is unreadable on its own."""
    recent = [str(h) for h in list(history)[-CONTEXT_TURNS:]]
    lines = [f"- {h}" for h in recent]
    lines.append(f"- LAST USER MESSAGE: {message}")
    return "\n".join(lines)


def assess(message: str, history: Sequence[str] = (), classifier=None) -> bool:
    """True when this message should get the crisis response.

    Classifier first, deterministic fallback on ANY failure — including a
    slow one. A hung model call must not hold a distressed person on a
    spinner, so the call runs under a hard timeout and a timeout is treated
    exactly like an error: fall back, fail safe.

    Note the asymmetry in the fallback: it uses the BROAD net, so an outage
    makes us over-cautious rather than blind.
    """
    classify = classifier or _default_classifier
    try:
        future = _CLASSIFIER_POOL.submit(classify, build_context(message, history))
        # NOT a `with ThreadPoolExecutor(...)` block: its __exit__ calls
        # shutdown(wait=True), which blocks until the hung call returns and
        # makes the timeout useless. Abandon the future instead — the
        # worker finishes into the void and the pool bounds the damage.
        return future.result(timeout=settings().crisis_timeout_seconds) == "crisis"
    except Exception:
        # never let a model outage or a slow call decide someone doesn't
        # get help
        return deterministic_crisis(message)


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
