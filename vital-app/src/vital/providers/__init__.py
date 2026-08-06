"""Wearable sleep providers.

One seam, many devices. `forecast.py` consumes a source-agnostic list of
Night objects, so a provider's whole job is to turn somebody's API into
that shape — which is what makes Oura, Whoop or Garmin an adapter file
rather than a rewrite.

The seam is deliberately narrow. Everything provider-specific (OAuth
endpoints, response shapes, token quirks) lives inside the adapter;
everything shared (encrypted token storage, sync scheduling, connection
status, failure reporting) lives outside it in sync.py. That boundary is
the point: LIMITATIONS.md records that six community APIs closed in six
years, and wearable APIs are the same class of dependency — the Fitbit Web
API is being decommissioned mid-2026 with no token migration. Providers
here should be assumed to be temporary.
"""
from vital.providers.base import (ProviderAuthError, ProviderError,
                                  ProviderUnavailable, SleepProvider)

__all__ = ["SleepProvider", "ProviderError", "ProviderAuthError",
           "ProviderUnavailable", "get_provider", "PROVIDERS"]


def get_provider(name: str) -> SleepProvider:
    if name not in PROVIDERS:
        raise KeyError(f"unknown provider {name!r}")
    return PROVIDERS[name]()


def _google_health():
    from vital.providers.google_health import GoogleHealthProvider
    return GoogleHealthProvider()


# Lazily constructed so importing this package never touches config or the
# network — tests import it constantly.
PROVIDERS = {"google-health": _google_health}
