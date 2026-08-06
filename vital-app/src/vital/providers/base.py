"""The provider contract.

Four methods. Anything a second provider would need differently lives
behind them; anything shared stays in sync.py.

The error taxonomy matters more than it looks. Two failures need to be
told apart everywhere upstream:

  ProviderAuthError    the user must reconnect. Nothing we retry will fix
                       it. In Google's Testing publishing status this
                       happens every 7 days by design, so it is the normal
                       case, not an exception.
  ProviderUnavailable  transient — network, 5xx, rate limit. Retrying
                       later is correct and the user need not be told.

Collapsing these was how the Reddit tool stayed dead for months: a
permanent failure reported as "temporarily down" is indistinguishable from
a blip, and nobody ever reconnects anything.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from vital.forecast import Night


class ProviderError(RuntimeError):
    """Base for anything a provider can go wrong with."""


class ProviderAuthError(ProviderError):
    """The connection is broken and the USER must act. Surfaced in the UI."""


class ProviderUnavailable(ProviderError):
    """Transient. Log it, retry later, do not nag the user."""


@runtime_checkable
class SleepProvider(Protocol):
    name: str
    label: str            # shown in the UI, e.g. "Fitbit / Pixel Watch"
    source_tag: str       # written to health_sleep.source; scopes deletion

    def configured(self) -> bool:
        """Whether credentials exist for this provider.

        Checked before showing a Connect button, so a user is never sent
        into a consent screen that cannot complete.
        """

    def authorize_url(self, state: str) -> str:
        """Consent URL. `state` is opaque here and verified by the caller."""

    def exchange_code(self, code: str) -> tuple[str, str]:
        """Authorization code -> (refresh_token, granted_scopes)."""

    def access_token(self, refresh_token: str) -> tuple[str, int]:
        """refresh_token -> (access_token, seconds_until_expiry)."""

    def fetch_nights(self, access_token: str, since: date) -> list[Night]:
        """Sleep since `since`, already normalised to Night.

        Providers return their own shapes; converting here rather than in
        sync.py is what keeps the forecast ignorant of who supplied its
        data.
        """

    def revoke(self, refresh_token: str) -> None:
        """Best-effort upstream revocation on disconnect.

        Deleting our row is not enough: the token stays live at the
        provider until revoked, and Google's user data policy expects a
        real disconnect. Failure here must not block local deletion.
        """
