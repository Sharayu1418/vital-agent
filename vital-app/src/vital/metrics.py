"""Request metrics as structured JSON logs (Phase 4).

Cloud Logging ingests JSON lines natively → log-based metrics → dashboards,
no metrics client library needed at this scale. LangSmith covers the
per-hop trace view; these are the aggregates you alert on.

user_id is hashed — logs must never contain raw identity (anon session ids
are identity too).
"""
import hashlib
import json
import logging
import sys

logger = logging.getLogger("vital.metrics")
# Deliberate stdout emission: uvicorn/Cloud Run don't configure app loggers,
# so without this the JSON lines are silently dropped (review finding).
if not logger.handlers:
    logger.addHandler(logging.StreamHandler(sys.stdout))
logger.setLevel(logging.INFO)
logger.propagate = False  # avoid double lines if root logging IS configured


def _hash(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:10]


def log_turn(user_id: str, thread_id: str, routing_hops: int,
             est_tokens: int, duration_ms: int, kind: str = "chat_turn",
             heuristic_tokens: int | None = None,
             routes: list[str] | None = None) -> None:
    """est_tokens is the BILLED figure — real provider usage summed across
    every model call in the turn, falling back to the chars/4 heuristic when
    no usage metadata is available.

    heuristic_tokens carries the old estimate alongside it, so the ratio
    between the two is visible in logs. That ratio is how you decide what
    DAILY_TOKEN_BUDGET should actually be; the heuristic was undercounting,
    so the cap was looser in practice than the config implied.
    """
    payload = {
        "metric": kind,
        "user": _hash(user_id),
        "thread": _hash(thread_id),
        "routing_hops": routing_hops,   # histogram → loop regressions
        "est_tokens": est_tokens,       # sum by user → cost curve
        "duration_ms": duration_ms,     # p95 → latency target (<2500 TTFT)
    }
    if heuristic_tokens is not None:
        payload["heuristic_tokens"] = heuristic_tokens
        payload["undercount_ratio"] = round(est_tokens / max(1, heuristic_tokens), 2)
    if routes is not None:
        # WHICH agents ran, not just how many hops. A count of 5 hid the fact
        # that it was the same specialist re-invoked on the same message;
        # ["sleep_energy", "sleep_energy", ...] would have said so immediately.
        payload["routes"] = routes
    logger.info(json.dumps(payload))
