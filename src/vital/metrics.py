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
             est_tokens: int, duration_ms: int, kind: str = "chat_turn") -> None:
    logger.info(json.dumps({
        "metric": kind,
        "user": _hash(user_id),
        "thread": _hash(thread_id),
        "routing_hops": routing_hops,   # histogram → loop regressions
        "est_tokens": est_tokens,       # sum by user → cost curve
        "duration_ms": duration_ms,     # p95 → latency target (<2500 TTFT)
    }))
