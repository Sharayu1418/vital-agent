"""Request rate limiting.

WHAT WAS MISSING
----------------
The per-user daily TOKEN budget caps model spend, but nothing capped
requests. Cloud Run autoscales, so a loop against /chat was a bill rather
than an error — and endpoints that never touch a model (/session,
/activity-posts) had no ceiling at all.

WHAT THIS IS, HONESTLY
----------------------
An in-process sliding window. On Cloud Run with several instances, each
holds its own counters, so the effective limit is roughly the configured
one times the instance count. That is a real weakness and it is written
here rather than discovered later.

It is still worth having. The attack it stops is a single client in a loop,
which is what actually happens, and that client gets pinned to one instance
for long enough to be throttled. What it does NOT stop is a distributed
flood — that needs Cloud Armor or a shared Redis counter, and neither is
proportionate to an app with one user.

Chosen over slowapi to avoid a dependency for eighty lines of dict
arithmetic, and because the failure mode of a limiter you cannot read is
worse than not having one.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

# (requests, seconds). Generous on purpose: these exist to stop a runaway
# loop, not to police normal use. A human cannot hit any of these.
LIMITS: dict[str, tuple[int, int]] = {
    "chat": (20, 60),        # a model call each; the token budget is the real cap
    "write": (60, 60),       # posts, requests, feedback, settings
    "read": (240, 60),       # panel polling — five endpoints per app load
    "connect": (10, 300),    # OAuth starts; nobody links a device twice a minute
}

_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_lock = Lock()

# Stop the dict growing without bound. Anything older than the longest
# window is dead weight, and a limiter that leaks memory is a slower
# version of the problem it was added to fix.
_MAX_KEYS = 10_000


def check(bucket: str, identity: str, now: float | None = None) -> tuple[bool, int]:
    """(allowed, retry_after_seconds).

    `identity` is the resolved user id — never anything the client sends.
    Keying on a client-supplied value would let anybody reset their own
    counter by changing a header.
    """
    limit, window = LIMITS.get(bucket, LIMITS["read"])
    now = now if now is not None else time.monotonic()
    key = (bucket, identity)

    with _lock:
        if len(_hits) > _MAX_KEYS:
            _evict(now)
        stamps = _hits[key]
        while stamps and now - stamps[0] > window:
            stamps.popleft()
        if len(stamps) >= limit:
            return False, max(1, int(window - (now - stamps[0])) + 1)
        stamps.append(now)
        return True, 0


def _evict(now: float) -> None:
    """Drop buckets with nothing live in them. Caller holds the lock."""
    longest = max(window for _, window in LIMITS.values())
    for key in [k for k, v in _hits.items() if not v or now - v[-1] > longest]:
        del _hits[key]


def reset() -> None:
    """Tests only. Rate limits are global state; without this one test's
    traffic makes the next one fail for reasons unrelated to its subject."""
    with _lock:
        _hits.clear()
