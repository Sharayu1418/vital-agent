"""The scheduled run: who gets a brief right now, and did they already.

Cloud Scheduler calls this HOURLY, not daily, because "7am" is twenty-four
different instants. Each run picks out the users for whom it is currently
their chosen hour, in their own clock.

THE TWO RULES THAT KEEP THIS FROM BECOMING SPAM
-----------------------------------------------
1. **One per day, enforced by data.** `last_brief_date` is checked and
   written per user. Cloud Scheduler retries on timeout and can fire twice;
   nothing erodes trust faster than the same notification arriving again.
2. **Silence is allowed.** compose() returns None when there is nothing
   worth saying, and this respects that rather than padding it out. A
   notification containing filler trains people to ignore the next one, and
   the next one might have mattered.

Failures are per-user. One person's dead subscription, missing sleep data
or provider outage must not stop everybody else's brief — so every user is
wrapped, and the run reports counts rather than raising.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vital import brief as brief_mod
from vital import forecast as engine
from vital import metrics, push, storage


def due(prefs: dict, now_utc: datetime) -> bool:
    """Is it this user's brief hour, and have they not had one today?

    Both questions are answered in the USER's clock. Their calendar date is
    what "already had one today" means — using the server's would send two
    briefs to anyone west of Greenwich on the day the UTC date rolls over
    mid-evening.
    """
    if not prefs.get("brief_enabled"):
        return False
    local = now_utc + timedelta(minutes=int(prefs.get("utc_offset_min") or 0))
    if local.hour != int(prefs.get("brief_hour") or 7):
        return False
    return prefs.get("last_brief_date") != local.date().isoformat()


def build_for(user_id: str, local_now: datetime, name: str = ""):
    """Assemble one user's brief. Returns a Brief or None."""
    nights = engine.nights_from_rows(
        storage.sleep_history(engine.DEBT_WINDOW_NIGHTS * 2),
        storage.health_rows(user_id))
    forecast = engine.forecast(nights, local_now, horizon_hours=18)

    # Only today's committed events matter to a morning brief.
    today = local_now.date().isoformat()
    events = [e for e in storage.calendar_events(user_id)
              if str(e.get("day", "")).startswith(today)
              or str(e.get("day", "")) == local_now.strftime("%A")]

    return brief_mod.compose(local_now, nights, forecast, events, name=name)


def run(now_utc: datetime | None = None) -> dict:
    """One scheduled pass. Never raises.

    `now_utc` is injectable so the whole schedule can be tested without
    waiting for 7am.
    """
    now_utc = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    considered = sent = skipped = failed = 0

    for prefs in storage.brief_candidates():
        considered += 1
        if not due(prefs, now_utc):
            continue
        user_id = prefs["user_id"]
        try:
            storage.current_user_id.set(user_id)   # tools read identity here
            local = now_utc + timedelta(
                minutes=int(prefs.get("utc_offset_min") or 0))
            brief = build_for(user_id, local,
                              name=prefs.get("display_name") or "")
            if brief is None:
                skipped += 1
                # Still stamp the date. Re-evaluating an empty brief every
                # hour for the rest of the day is pointless work, and the
                # answer will not change.
                storage.save_prefs(user_id,
                                   last_brief_date=local.date().isoformat())
                continue

            push.send(user_id, brief.title, brief.body, url="/")
            storage.save_prefs(user_id,
                               last_brief_date=local.date().isoformat())
            sent += 1
        except Exception as exc:
            failed += 1
            metrics.log_tool(user_id, "brief:run", "error",
                             error=type(exc).__name__)

    summary = {"considered": considered, "sent": sent,
               "nothing_to_say": skipped, "failed": failed}
    metrics.log_tool("system", "brief:run",
                     "ok" if not failed else "error",
                     error=None if not failed else f"{failed} users failed")
    return summary
