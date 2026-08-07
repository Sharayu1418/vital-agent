"""Web push delivery.

Transport only — what to say lives in brief.py, when to say it lives in the
job. This file knows how to get a short string onto somebody's lock screen
and nothing else.

WHY WEB PUSH AND NOT EMAIL
--------------------------
No new vendor, no deliverability problem, no email address to store and
protect. The cost is that iOS Safari only supports it for installed PWAs,
which is a real limitation and is documented rather than hidden.

SUBSCRIPTIONS DIE, AND THAT IS NORMAL
-------------------------------------
Browsers expire push subscriptions routinely — cleared site data, a long
absence, a reinstall. The push service reports this as 404 or 410, and the
correct response is to delete the row, not to retry it every morning
forever. A dead subscription retried daily is the same shape as the Reddit
integration: a permanent failure that looks like a transient one.
"""
from __future__ import annotations

import json

from vital import metrics, storage
from vital.config import settings


class PushUnavailable(RuntimeError):
    """Push is not configured on this deployment."""


def configured() -> bool:
    cfg = settings()
    return bool(cfg.vapid_public_key and cfg.vapid_private_key
                and cfg.vapid_subject)


def public_key() -> str:
    """Handed to the browser so it can subscribe. Public by design."""
    return settings().vapid_public_key or ""


def send(user_id: str, title: str, body: str, url: str = "/") -> dict:
    """Deliver to every device this user has registered.

    Returns a summary rather than raising: the caller is a scheduled job
    running for many users, and one person's dead subscription must not
    stop everybody else's brief.
    """
    if not configured():
        raise PushUnavailable("VAPID keys are not configured")

    subscriptions = storage.push_subscriptions(user_id)
    if not subscriptions:
        return {"sent": 0, "reason": "no subscriptions"}

    payload = json.dumps({"title": title, "body": body, "url": url})
    sent = failed = removed = 0
    for subscription in subscriptions:
        outcome = _deliver(user_id, subscription, payload)
        if outcome == "ok":
            sent += 1
        elif outcome == "gone":
            removed += 1
        else:
            failed += 1

    metrics.log_tool(user_id, "push:send",
                     "ok" if sent else "error",
                     error=None if sent else f"{failed} failed, {removed} expired")
    return {"sent": sent, "failed": failed, "removed": removed}


def _deliver(user_id: str, subscription: dict, payload: str) -> str:
    """One device. Returns "ok" | "gone" | "failed"."""
    from pywebpush import WebPushException, webpush

    cfg = settings()
    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": {"p256dh": subscription["p256dh"],
                         "auth": subscription["auth"]},
            },
            data=payload,
            vapid_private_key=cfg.vapid_private_key,
            vapid_claims={"sub": cfg.vapid_subject},
            timeout=10,
        )
        return "ok"
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            # The subscription is permanently dead. Delete it rather than
            # retrying every morning until the end of time.
            storage.delete_push_subscription(user_id, subscription["endpoint"])
            return "gone"
        return "failed"
    except Exception:
        return "failed"
