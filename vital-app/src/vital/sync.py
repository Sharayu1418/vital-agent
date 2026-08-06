"""Provider-agnostic half of wearable sync.

Everything here works the same for Fitbit, Oura or Whoop: refresh a token,
pull nights, write them, record what happened. Everything that differs
lives behind SleepProvider.

THE FAILURE THIS FILE EXISTS TO PREVENT
---------------------------------------
In Google's Testing publishing status refresh tokens expire after SEVEN
DAYS. Sync will therefore break on a schedule, by design, for as long as
the app is unverified. A background job that stops working and only says
so in the logs is precisely how the Reddit integration stayed dead for
months. So every outcome is recorded on the connection row, the panel
reads it, and an expired authorization becomes a visible "Reconnect"
rather than an absence of data.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, timedelta, timezone

from vital import metrics, providers, storage
from vital.config import settings
from vital.providers.base import ProviderAuthError, ProviderError

# Google's guidance: a fast "hot" load first, deeper history later. 14 is
# also exactly the forecast's debt window, so this is the point at which
# confidence stops improving for v1.
INITIAL_DAYS = 14
# Access tokens last about an hour. Refresh a little early so a sync never
# starts with a token that expires mid-flight.
EXPIRY_MARGIN_S = 120


def status(user_id: str, provider_name: str = "google-health") -> dict:
    """What the panel renders. Never touches the network.

    Returns a plain shape so the UI does not have to know about token
    lifetimes: connected, last_sync, and needs_reconnect.
    """
    try:
        provider = providers.get_provider(provider_name)
    except KeyError:
        return {"provider": provider_name, "available": False}

    row = storage.get_connection(user_id, provider_name)
    if not row:
        return {"provider": provider_name, "label": provider.label,
                "available": provider.configured(), "connected": False}

    error = row.get("last_error") or ""
    return {
        "provider": provider_name,
        "label": provider.label,
        "available": True,
        "connected": True,
        "connected_at": row.get("connected_at"),
        "last_sync_at": row.get("last_sync_at"),
        # The distinction the UI acts on. An auth failure needs the user;
        # anything else will fix itself and should not nag them.
        "needs_reconnect": error.startswith("auth:"),
        "last_error": error.split(":", 1)[-1] if error else None,
    }


def _fresh_access_token(user_id: str, provider) -> str:
    cached = storage.get_cached_access_token(user_id, provider.name)
    if cached:
        token, expires_at = cached
        try:
            if datetime.fromisoformat(expires_at) > datetime.now(timezone.utc):
                return token
        except (TypeError, ValueError):
            pass                          # unparseable expiry: just refresh

    refresh_token = storage.get_refresh_token(user_id, provider.name)
    if not refresh_token:
        raise ProviderAuthError("no saved authorization — connect first.")

    token, expires_in = provider.access_token(refresh_token)
    expires_at = (datetime.now(timezone.utc)
                  + timedelta(seconds=max(60, expires_in - EXPIRY_MARGIN_S)))
    storage.cache_access_token(user_id, provider.name, token,
                               expires_at.isoformat())
    return token


def sync(user_id: str, provider_name: str = "google-health",
         days: int = INITIAL_DAYS) -> dict:
    """Pull recent nights and store them. Returns a result dict.

    Never raises. Callers are the forecast endpoint and a user-facing
    button, and neither should fail because a third party is having a bad
    day — the outcome is reported instead.
    """
    started = _time.monotonic()
    try:
        provider = providers.get_provider(provider_name)
    except KeyError:
        return {"error": f"unknown provider {provider_name}"}

    if not storage.get_connection(user_id, provider_name):
        return {"not_connected": True}

    try:
        token = _fresh_access_token(user_id, provider)
        since = date.today() - timedelta(days=days)
        nights = provider.fetch_nights(token, since)
    except ProviderAuthError as exc:
        # Prefixed so status() can tell "the user must act" from "try
        # again later" without re-deriving it from the message text.
        storage.mark_sync(user_id, provider_name, f"auth:{exc}")
        _log(user_id, provider_name, "error", str(exc), started)
        return {"error": str(exc), "needs_reconnect": True}
    except ProviderError as exc:
        storage.mark_sync(user_id, provider_name, f"temp:{exc}")
        _log(user_id, provider_name, "error", str(exc), started)
        return {"error": str(exc)}
    except Exception as exc:                       # never take the caller down
        storage.mark_sync(user_id, provider_name, f"temp:{type(exc).__name__}")
        _log(user_id, provider_name, "error", type(exc).__name__, started)
        return {"error": f"sync failed ({type(exc).__name__})"}

    rows = [{"date": n.date, "duration_min": n.duration_min,
             "quality": str(n.quality) if n.quality is not None else "",
             "source": provider.source_tag}
            for n in nights]
    if rows:
        storage.save_health_rows(user_id, rows)
    storage.mark_sync(user_id, provider_name)
    _log(user_id, provider_name, "ok", None, started)
    return {"synced": len(rows), "since": (date.today()
                                           - timedelta(days=days)).isoformat()}


def sync_if_stale(user_id: str, provider_name: str = "google-health") -> None:
    """Best-effort refresh before serving a forecast.

    Wearables post last night's sleep once, in the morning, so polling
    harder than a few hours spends rate limit for nothing.
    """
    row = storage.get_connection(user_id, provider_name)
    if not row:
        return
    if (row.get("last_error") or "").startswith("auth:"):
        return          # broken authorization; retrying cannot help

    last = row.get("last_sync_at")
    if last:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
            if age < timedelta(minutes=settings().sync_max_age_minutes):
                return
        except (TypeError, ValueError):
            pass
    sync(user_id, provider_name)


def disconnect(user_id: str, provider_name: str = "google-health") -> dict:
    """Revoke upstream, delete the token, erase what the provider wrote.

    Three separate things, and all of them required. Dropping our row
    leaves the token live at Google; keeping the data after a disconnect
    fails the user data policy CASA assesses against. Scoped by source, so
    manual logs and the user's own uploaded export survive.
    """
    try:
        provider = providers.get_provider(provider_name)
    except KeyError:
        return {"error": f"unknown provider {provider_name}"}

    try:
        refresh_token = storage.get_refresh_token(user_id, provider_name)
    except Exception:
        refresh_token = None        # unreadable token still gets deleted

    if refresh_token:
        provider.revoke(refresh_token)          # best effort by contract

    storage.delete_connection(user_id, provider_name)
    removed = storage.delete_synced_health_data(user_id, provider.source_tag)
    metrics.log_tool(user_id, f"sync:{provider_name}:disconnect", "ok")
    return {"disconnected": True, "deleted_nights": removed}


def _log(user_id: str, provider_name: str, outcome: str,
         error: str | None, started: float) -> None:
    """Route sync through the same tool-health metric as everything else,
    so the alert policy already watching for a dead tool sees a dead sync
    too — no second observability path to remember to build."""
    metrics.log_tool(user_id, f"sync:{provider_name}", outcome,
                     error=error,
                     duration_ms=int((_time.monotonic() - started) * 1000))
