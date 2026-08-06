"""Google Health API — Fitbit and Pixel Watch sleep.

Formerly the Fitbit Web API, which is decommissioned in September 2026 with
no OAuth token migration, so there is no version of this that builds on the
old one.

WHAT THIS UNLOCKS
-----------------
Uploaded Apple Health exports give duration and nothing else, so the
forecast's circadian phase is a population default and confidence sits
around 0.21. This returns `startUtcOffset` and `endUtcOffset` alongside
every interval, which means real local bedtimes and wake times — the
forecast's timing becomes the user's own.

THREE THINGS THE DOCS MAKE EASY TO GET WRONG
--------------------------------------------
1. `metadata.main` separates the night's sleep from naps. Without that
   filter an afternoon nap is stored as a night, which corrupts both sleep
   debt and the wake-time average the whole curve is keyed to.
2. `minutesAsleep` is not `minutesInSleepPeriod`. The latter is time in
   bed; using it would understate sleep debt for anyone who lies awake.
3. Sleep pages cap at 25 records, default and maximum. Two weeks fits, but
   any deeper history silently truncates without pagination.

Numbers arrive as JSON strings ("407") because the underlying protos are
int64. Everything is parsed rather than trusted.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, timedelta

import httpx

from vital.config import settings
from vital.forecast import Night
from vital.providers.base import ProviderAuthError, ProviderUnavailable

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_ROOT = "https://health.googleapis.com/v4"
SCOPE = "https://www.googleapis.com/auth/googlehealth.sleep.readonly"

# Google's reconciled stream, restricted to tracker devices. Excludes
# manually logged sleep, which the user may also be entering into VITAL
# directly — double-counting a night would inflate their sleep and hide
# real debt.
SOURCE_FAMILY = "users/me/dataSourceFamilies/google-wearables"

PAGE_SIZE = 25          # the documented cap for sleep, not a choice
MAX_PAGES = 12          # ~300 nights; a guard against a pagination loop
TIMEOUT = 20.0


class GoogleHealthProvider:
    name = "google-health"
    label = "Fitbit / Pixel Watch"
    source_tag = "google-health"

    # ---------- configuration ----------

    def configured(self) -> bool:
        cfg = settings()
        return bool(cfg.google_health_client_id
                    and cfg.google_health_client_secret
                    and cfg.google_health_redirect_uri)

    # ---------- OAuth ----------

    def authorize_url(self, state: str) -> str:
        from urllib.parse import urlencode

        cfg = settings()
        params = {
            "client_id": cfg.google_health_client_id,
            "redirect_uri": cfg.google_health_redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            # offline + consent is what produces a refresh token at all.
            # Without prompt=consent Google returns none on a repeat
            # authorization, and the connection silently cannot be renewed.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> tuple[str, str]:
        cfg = settings()
        payload = {
            "code": code,
            "client_id": cfg.google_health_client_id,
            "client_secret": cfg.google_health_client_secret,
            "redirect_uri": cfg.google_health_redirect_uri,
            "grant_type": "authorization_code",
        }
        data = self._token_call(payload)
        refresh = data.get("refresh_token")
        if not refresh:
            # Almost always prompt=consent missing, or a re-authorization
            # where Google reuses the earlier grant. Storing the access
            # token alone would work for an hour and then break forever.
            raise ProviderAuthError(
                "Google returned no refresh token — the connection could "
                "not be made durable. Try disconnecting and reconnecting.")
        return refresh, data.get("scope", SCOPE)

    def access_token(self, refresh_token: str) -> tuple[str, int]:
        cfg = settings()
        data = self._token_call({
            "client_id": cfg.google_health_client_id,
            "client_secret": cfg.google_health_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        token = data.get("access_token")
        if not token:
            raise ProviderAuthError("no access token in Google's response")
        return token, int(data.get("expires_in", 3600))

    def _token_call(self, payload: dict) -> dict:
        try:
            response = httpx.post(TOKEN_URL, data=payload, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"token endpoint unreachable "
                                      f"({type(exc).__name__})") from exc

        if response.status_code == 400:
            # invalid_grant is the one that matters: the refresh token is
            # dead. In Testing publishing status Google expires them after
            # 7 DAYS, so this is routine rather than exceptional, and it
            # must reach the user as "reconnect" rather than a retry.
            detail = ""
            try:
                detail = response.json().get("error", "")
            except Exception:
                pass
            raise ProviderAuthError(
                "Google rejected the saved authorization"
                + (f" ({detail})" if detail else "")
                + ". Reconnect to continue syncing.")
        if response.status_code in (401, 403):
            raise ProviderAuthError("Google denied the request — reconnect.")
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable(f"Google returned {response.status_code}")
        if response.status_code != 200:
            raise ProviderUnavailable(f"unexpected {response.status_code} "
                                      "from the token endpoint")
        return response.json()

    def revoke(self, refresh_token: str) -> None:
        """Best effort. Failure must not block local deletion — a user
        asking to disconnect gets disconnected either way."""
        try:
            httpx.post(REVOKE_URL, data={"token": refresh_token},
                       timeout=TIMEOUT)
        except httpx.HTTPError:
            pass

    # ---------- data ----------

    def fetch_nights(self, access_token: str, since: date) -> list[Night]:
        url = f"{API_ROOT}/users/me/dataTypes/sleep/dataPoints:reconcile"
        params = {
            "dataSourceFamily": SOURCE_FAMILY,
            # civil_end_time, not start: a night beginning 2026-03-03 ends
            # on the 4th, and filtering on start would drop the most recent
            # night whenever the boundary falls mid-sleep.
            "filter": f'sleep.interval.civil_end_time >= "{since.isoformat()}"',
            "pageSize": PAGE_SIZE,
        }
        headers = {"Authorization": f"Bearer {access_token}",
                   "Accept": "application/json"}

        nights: dict[str, Night] = {}
        token = None
        for _ in range(MAX_PAGES):
            if token:
                params["pageToken"] = token
            payload = self._get(url, params, headers)
            for point in payload.get("dataPoints") or []:
                night = self._to_night(point)
                if night:
                    nights[night.date] = night
            token = payload.get("nextPageToken")
            if not token:
                break
        return [nights[day] for day in sorted(nights)]

    def _get(self, url: str, params: dict, headers: dict) -> dict:
        """One request, with backoff on the two codes Google names.

        Their docs are explicit that immediate retries on 429 and 504
        compound congestion, so this waits rather than hammering.
        """
        delay = 1.0
        for attempt in range(4):
            try:
                response = httpx.get(url, params=params, headers=headers,
                                     timeout=TIMEOUT)
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(
                    f"Google Health unreachable ({type(exc).__name__})") from exc

            if response.status_code in (401, 403):
                raise ProviderAuthError(
                    "Google rejected the access token — reconnect.")
            if response.status_code in (429, 504) or response.status_code >= 500:
                if attempt == 3:
                    raise ProviderUnavailable(
                        f"Google Health returned {response.status_code} after "
                        "retries")
                _time.sleep(delay)
                delay *= 2
                continue
            if response.status_code != 200:
                raise ProviderUnavailable(
                    f"unexpected {response.status_code} from Google Health")
            return response.json()
        raise ProviderUnavailable("exhausted retries against Google Health")

    @staticmethod
    def _to_night(point: dict) -> Night | None:
        sleep = (point or {}).get("sleep") or {}
        metadata = sleep.get("metadata") or {}
        # Naps are real sleep but they are not nights. Counting one would
        # move the wake-time average the entire curve is keyed to.
        if not metadata.get("main"):
            return None

        interval = sleep.get("interval") or {}
        start = _local(interval.get("startTime"), interval.get("startUtcOffset"))
        end = _local(interval.get("endTime"), interval.get("endUtcOffset"))
        if not start or not end:
            return None

        summary = sleep.get("summary") or {}
        minutes = _as_int(summary.get("minutesAsleep"))
        if not minutes:
            # Fall back to the interval only if the summary is absent;
            # elapsed time is a worse measure of sleep than minutesAsleep
            # and should never silently replace it.
            minutes = int((end - start).total_seconds() // 60)
        if not 30 <= minutes <= 18 * 60:
            return None                      # same plausibility gate as manual logs

        return Night(
            # Filed under the morning the user woke, matching how a manual
            # log is dated.
            date=end.date().isoformat(),
            duration_min=minutes,
            wake_time=end.time().replace(second=0, microsecond=0),
            bedtime=start.time().replace(second=0, microsecond=0),
            quality=_quality(summary))


def _as_int(value) -> int | None:
    """Google serialises int64 as a JSON string."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _local(timestamp: str | None, offset: str | None) -> datetime | None:
    """UTC instant plus the offset that was in force, as a local wall clock.

    The offset travels with each interval, which means a night slept in
    another timezone is reported in the clock the user was actually living
    in. Converting through the server's timezone instead would shift every
    bedtime.
    """
    if not timestamp:
        return None
    try:
        moment = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    seconds = 0
    if offset:
        try:
            seconds = int(str(offset).rstrip("s") or 0)
        except ValueError:
            seconds = 0
    return (moment + timedelta(seconds=seconds)).replace(tzinfo=None)


def _quality(summary: dict) -> int | None:
    """A 1-5 quality score from sleep efficiency, to match manual logs.

    Deliberately crude. Fitbit publishes its own sleep score, but it is not
    in this payload, and inventing a sophisticated-looking metric from
    stage data would imply a precision this mapping does not have. Time
    asleep over time in bed is a real, well-understood measure.
    """
    asleep = _as_int(summary.get("minutesAsleep"))
    in_bed = _as_int(summary.get("minutesInSleepPeriod"))
    if not asleep or not in_bed:
        return None
    efficiency = asleep / in_bed
    for threshold, score in ((0.95, 5), (0.90, 4), (0.85, 3), (0.75, 2)):
        if efficiency >= threshold:
            return score
    return 1
