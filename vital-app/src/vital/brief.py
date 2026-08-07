"""The morning brief — one sentence you can act on.

WHY THIS FILE IS MOSTLY RULES
-----------------------------
Everything VITAL knows — memory, the energy forecast, wearable sync — is
currently invisible to anyone who does not remember to open the tab. The
brief is what turns a tool into a habit.

Which also makes it the single easiest thing to ruin. A daily notification
has exactly one chance: the moment it feels like noise, it is turned off
and never turned back on. So the constraints below are enforced in code
rather than left to a prompt:

  - ONE adjustment, never a list. A dashboard is not actionable.
  - Under MAX_WORDS. If it needs scrolling it has already failed.
  - Never guilt. No streaks, no "you missed", no productivity shame.
  - Say nothing rather than say filler. A brief with no real content is
    worse than no brief, and returning None is a supported outcome.

Pure: takes data, returns text. No database, no network, no clock reads —
`now_local` is passed in, for the same reason the forecast takes it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_WORDS = 45          # tested; a notification longer than this is ignored
TITLE_MAX = 48          # roughly what a notification title shows before eliding


@dataclass(frozen=True)
class Brief:
    title: str
    body: str

    def word_count(self) -> int:
        return len(self.body.split())


def _hours(minutes: int | None) -> str:
    if not minutes:
        return ""
    return f"{minutes // 60}h{minutes % 60:02d}"


def _sleep_line(nights: list, target_h: float = 8.0) -> str:
    """Last night, and the short streak if there is one.

    States the number and stops. "You only got 6 hours!" is the tone this
    app exists not to have — the number is the information, the alarm is
    editorialising.
    """
    if not nights:
        return ""
    last = nights[-1]
    duration = _hours(getattr(last, "duration_min", None))
    if not duration:
        return ""

    streak = 0
    for night in reversed(nights):
        if (getattr(night, "duration_min", 0) or 0) < target_h * 60 - 30:
            streak += 1
        else:
            break

    if streak >= 3:
        return f"{duration} last night, {streak} short ones in a row."
    if streak == 2:
        return f"{duration} last night, second short one."
    return f"{duration} last night."


def _peak_line(forecast) -> str:
    """When to put the hard thing. The forecast's whole point."""
    if forecast is None or forecast.confidence < 0.25:
        return ""              # a population curve is not worth a notification
    peak = forecast.peak()
    if peak is None:
        return ""
    return f"Sharpest around {peak.at.strftime('%H:%M')}."


def daytime_dip(forecast, before_hour: int = 20):
    """The dip worth scheduling around — the afternoon one.

    forecast.trough() is the global minimum, which over a morning-to-bedtime
    horizon is always the hour before sleep. Telling someone their energy
    will be lowest at 00:30 is true, useless, and makes the whole brief look
    like it is reading a chart rather than understanding a day.
    """
    if forecast is None:
        return None
    daytime = [p for p in forecast.waking_points()
               if 9 <= p.at.hour < before_hour]
    return min(daytime, key=lambda p: p.energy) if daytime else None


def _adjustment(forecast, events: list) -> str:
    """The ONE thing to change today. Never two.

    Ordered by how much the change is worth, and it returns on the first
    match on purpose: a brief that lists three adjustments is a to-do list,
    and a to-do list arriving at 7am is the thing people mute.
    """
    if forecast is None:
        return ""

    # 1. Something demanding scheduled into the afternoon dip.
    trough = daytime_dip(forecast)
    if trough is not None and events:
        dip_hour = trough.at.hour
        for event in events:
            start = str(event.get("start") or "")
            if not start[:2].isdigit():
                continue
            if abs(int(start[:2]) - dip_hour) <= 1 and event.get("kind") in (
                    "activity", "idea_work"):
                return (f"“{event.get('title', 'That session')}” lands in your "
                        f"dip — move it earlier if you can.")

    # 2. Real sleep debt, nothing else pressing.
    if forecast.chronic_debt_h >= 6:
        return (f"You're carrying {forecast.chronic_debt_h:.0f}h of sleep debt "
                "— tonight is the one to protect.")

    # 3. A good peak and nothing claiming it.
    peak = forecast.peak()
    if peak is not None and not events:
        return f"Nothing booked at your peak — worth claiming {peak.at.strftime('%H:%M')}."

    return ""


def compose(now_local: datetime, nights: list, forecast, events: list | None = None,
            name: str = "") -> Brief | None:
    """The brief, or None when there is nothing worth saying.

    None is a first-class outcome. A notification that arrives with filler
    trains people to ignore the next one, and the next one might have
    mattered.
    """
    events = events or []
    parts = [p for p in (_sleep_line(nights), _peak_line(forecast)) if p]
    adjustment = _adjustment(forecast, events)

    # Nothing measured AND nothing to suggest: stay quiet.
    if not parts and not adjustment:
        return None
    # Something measured but nothing to do about it is also not worth a
    # notification — that is a dashboard, and the app already has one.
    if not adjustment:
        return None

    body = " ".join(parts + [adjustment])
    body = _trim(body)

    greeting = f"Morning, {name}" if name else "Morning"
    title = f"{greeting} — {now_local.strftime('%A')}"
    return Brief(title=title[:TITLE_MAX], body=body)


def _trim(text: str) -> str:
    """Hard word cap, cutting at a sentence boundary where possible.

    Enforced here rather than trusted to whoever writes the next line: the
    limit is the product decision, and a limit that lives in a comment is
    not a limit.
    """
    words = text.split()
    if len(words) <= MAX_WORDS:
        return text
    clipped = " ".join(words[:MAX_WORDS])
    cut = max(clipped.rfind("."), clipped.rfind("—"))
    return (clipped[:cut + 1] if cut > len(clipped) // 2 else clipped).strip()
