"""Energy forecasting (A-1 v1) — deterministic, no ML, no I/O.

Every other agent looks BACKWARD: sleep_energy reports debt against an 8h
target, analyze_sleep_data runs pandas over history. Nothing predicted
anything, while the product promise — an energy copilot — is a forecasting
promise. This module is the forward-looking half.

WHY A MODEL AND NOT A PROMPT
----------------------------
The Sleep & Energy prompt already asserts a peak "~3-5h after wake" and a
dip "~7-9h after wake". That is a folk summary of Borbély's two-process
model, restated to a language model as a fact it must remember. Numbers
inside a prompt cannot be tested, cannot vary per user, and cannot tell the
planner anything. The same claim as code can do all three.

THE MODEL
---------
Four named components, each individually defensible:

  inertia    sleep inertia — grogginess on waking, decaying over ~1h.
             This is what makes the mid-morning peak a peak rather than
             the curve simply starting at its maximum.
  pressure   Process S — homeostatic sleep pressure, accumulating the
             whole time you are awake and saturating (tau ~18h).
  circadian  Process C — the ~24h alertness rhythm.
  afternoon  the post-lunch dip, a real circadian feature and the thing
             users notice most.

energy = base + w_c*C - w_s*S - inertia - dip - debt

PURITY
------
No clock reads, no database, no network. `now_local` is passed in, which is
not fussiness: the server runs in UTC and the user's day is the only
meaningful frame. Wake times are already stored as the user reported them,
so the curve is expressed in their local clock with no conversion — but
"where am I on the curve right now" needs the caller's actual local time,
and the browser is the only component that reliably knows it.

CALIBRATION
-----------
v1 uses population constants. They are not guesses: CONSTANTS below was
solved numerically against the shape the product already claims, and
tests/test_forecast.py re-derives the shape from the constants and fails if
it drifts. v2 fits per-user coefficients once there is labelled
predicted-vs-actual data to fit against.

The confidence number matters as much as the curve. A forecast is
unfalsifiable in the short term, which makes confident nonsense the natural
failure mode — so confidence is driven by data sufficiency and is capped
below 1.0 on purpose. A population curve for a stranger should say so.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

# Solved numerically (scripts/tune_forecast.py) against the target shape:
# morning peak 3-5h after wake and the day's high, afternoon dip 7-9h, a
# partial evening rebound that does NOT exceed the morning peak, and a
# genuine late decline. test_forecast.py asserts all of that from these
# values, so changing one and keeping the tests green means the shape
# survived; changing one and breaking them means it did not.
BASE = 0.85
W_CIRCADIAN = 0.10
W_PRESSURE = 0.36
TAU_PRESSURE_H = 18.2      # Process S time constant while awake
INERTIA = 0.28             # depth of grogginess at the moment of waking
TAU_INERTIA_H = 0.9        # how fast it clears
CIRCADIAN_PEAK_H = 6.0     # hours after wake at which Process C peaks
DIP_DEPTH = 0.16
DIP_AT_H = 8.0
DIP_WIDTH_H = 1.7

TARGET_SLEEP_H = 8.0
DEBT_WINDOW_NIGHTS = 14
MAX_DEBT_PENALTY = 0.22    # a fully sleep-wrecked week, not a bad night
ACUTE_PENALTY = 0.10       # last night specifically, on top of the rolling debt

# Fallbacks when the user has told us nothing. Deliberately unremarkable.
DEFAULT_WAKE = time(7, 30)
DEFAULT_BEDTIME = time(23, 30)

CONFIDENCE_FLOOR = 0.10
CONFIDENCE_CEILING = 0.85   # v1 is a population model; it never gets to be sure


@dataclass(frozen=True)
class Night:
    """One night, from either source.

    `wake_time` is None for uploaded rows: Apple Health exports give
    duration but not phase. Such a night still counts toward sleep debt
    while contributing nothing to circadian phase, and the two are tracked
    separately because they degrade differently.
    """
    date: str                       # ISO date
    duration_min: int
    wake_time: time | None = None
    bedtime: time | None = None
    quality: int | None = None      # 1-5, manual logs only


@dataclass(frozen=True)
class Point:
    at: datetime
    hours_awake: float | None       # None while asleep
    energy: float
    asleep: bool
    drivers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Forecast:
    points: list[Point]
    confidence: float
    basis: str
    typical_wake: time
    typical_bedtime: time
    chronic_debt_h: float
    acute_deficit_h: float

    def waking_points(self) -> list[Point]:
        return [p for p in self.points if not p.asleep]

    def peak(self) -> Point | None:
        awake = self.waking_points()
        return max(awake, key=lambda p: p.energy) if awake else None

    def trough(self) -> Point | None:
        awake = self.waking_points()
        return min(awake, key=lambda p: p.energy) if awake else None


# ---------- the curve ----------

def energy_at(hours_awake: float, debt_penalty: float = 0.0) -> float:
    """Predicted energy in [0, 1] this many hours after waking.

    Pure arithmetic over the module constants — the one place the shape is
    defined. Everything else in this file arranges inputs for it.
    """
    if hours_awake < 0:
        hours_awake = 0.0
    pressure = 1 - math.exp(-hours_awake / TAU_PRESSURE_H)
    circadian = math.cos(2 * math.pi * (hours_awake - CIRCADIAN_PEAK_H) / 24)
    inertia = INERTIA * math.exp(-hours_awake / TAU_INERTIA_H)
    dip = DIP_DEPTH * math.exp(
        -((hours_awake - DIP_AT_H) ** 2) / (2 * DIP_WIDTH_H ** 2))

    value = (BASE
             + W_CIRCADIAN * circadian
             - W_PRESSURE * pressure
             - inertia
             - dip
             - debt_penalty)
    return max(0.0, min(1.0, value))


def _drivers(hours_awake: float, debt_penalty: float) -> list[str]:
    """Why this point is where it is, strongest first.

    The planner quotes these. A forecast that cannot explain itself is
    indistinguishable from a number the model invented, which is exactly
    what this feature exists to replace.
    """
    out: list[tuple[float, str]] = []

    inertia = INERTIA * math.exp(-hours_awake / TAU_INERTIA_H)
    if inertia > 0.04:
        out.append((inertia, "still shaking off sleep inertia"))

    dip = DIP_DEPTH * math.exp(
        -((hours_awake - DIP_AT_H) ** 2) / (2 * DIP_WIDTH_H ** 2))
    if dip > 0.04:
        out.append((dip, "afternoon circadian dip"))

    pressure = W_PRESSURE * (1 - math.exp(-hours_awake / TAU_PRESSURE_H))
    if pressure > 0.12:
        out.append((pressure, f"{hours_awake:.0f}h of accumulated time awake"))

    circadian = W_CIRCADIAN * math.cos(
        2 * math.pi * (hours_awake - CIRCADIAN_PEAK_H) / 24)
    if circadian > 0.05:
        out.append((circadian, "circadian rhythm working in your favour"))

    if debt_penalty > 0.03:
        out.append((debt_penalty, "carrying sleep debt"))

    if not out:
        out.append((1.0, "well rested, low time awake"))
    return [text for _, text in sorted(out, key=lambda p: -p[0])]


# ---------- reading the history ----------

def _parse_time(value) -> time | None:
    if isinstance(value, time):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").time()
    except ValueError:
        return None


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _circular_mean(times: list[time]) -> time:
    """Average clock times on a circle.

    A naive mean of 23:50 and 00:10 is 12:00 — the middle of the following
    day, and the worst possible answer. Bedtimes straddle midnight
    constantly, so the mean has to go around the clock.
    """
    angles = [2 * math.pi * _minutes(t) / 1440 for t in times]
    x = sum(math.cos(a) for a in angles) / len(angles)
    y = sum(math.sin(a) for a in angles) / len(angles)
    angle = math.atan2(y, x) % (2 * math.pi)
    total = int(round(angle * 1440 / (2 * math.pi))) % 1440
    return time(total // 60, total % 60)


def _spread_minutes(times: list[time]) -> float:
    """Dispersion of clock times, again circularly.

    Uses the resultant length R: 1.0 means perfectly consistent, 0 means
    uniformly scattered. Converted to a minutes-like number so the caller
    can reason about it in familiar units.
    """
    if len(times) < 2:
        return 0.0
    angles = [2 * math.pi * _minutes(t) / 1440 for t in times]
    x = sum(math.cos(a) for a in angles) / len(angles)
    y = sum(math.sin(a) for a in angles) / len(angles)
    resultant = math.hypot(x, y)
    if resultant >= 0.9999:
        return 0.0
    circular_sd = math.sqrt(-2 * math.log(resultant))       # radians
    return circular_sd * 1440 / (2 * math.pi)


def sleep_debt(nights: list[Night],
               target_h: float = TARGET_SLEEP_H) -> tuple[float, float]:
    """(chronic_debt_h, acute_deficit_h) over the debt window.

    Chronic is the rolling shortfall; acute is last night alone. They are
    separated because they feel different and act differently: one bad
    night is a dip, a fortnight of them is a lower ceiling all day.

    Surplus counts. Sleeping in genuinely repays some debt, so a night over
    target reduces the total rather than being clamped at zero — clamping
    would make debt monotonically increasing and the forecast would sink
    forever.
    """
    if not nights:
        return 0.0, 0.0
    window = sorted(nights, key=lambda n: n.date, reverse=True)[:DEBT_WINDOW_NIGHTS]
    chronic = sum(target_h - (n.duration_min / 60) for n in window)
    chronic = max(0.0, chronic)          # a well-slept fortnight is not credit
    acute = max(0.0, target_h - (window[0].duration_min / 60))
    return round(chronic, 2), round(acute, 2)


def debt_penalty(chronic_h: float, acute_h: float) -> float:
    """Map debt onto a drop in the curve, saturating.

    Saturating matters: without it, a month of bad sleep would drive
    predicted energy to zero and the forecast would stop discriminating
    between "tired" and "catastrophic", which is precisely when it needs to.
    """
    chronic_part = MAX_DEBT_PENALTY * min(chronic_h, 16.0) / 16.0
    acute_part = ACUTE_PENALTY * min(acute_h, 3.0) / 3.0
    return round(chronic_part + acute_part, 4)


def wake_pattern(nights: list[Night]) -> tuple[time, time, int, float]:
    """(typical_wake, typical_bedtime, nights_with_phase, wake_spread_min).

    Only manually logged nights carry clock times. Uploaded Apple Health
    rows are duration-only, so a user who has only ever uploaded gets
    population phase — correct behaviour, and the confidence score is what
    communicates it.
    """
    wakes = [n.wake_time for n in nights if n.wake_time]
    beds = [n.bedtime for n in nights if n.bedtime]
    typical_wake = _circular_mean(wakes) if wakes else DEFAULT_WAKE
    typical_bed = _circular_mean(beds) if beds else DEFAULT_BEDTIME
    return typical_wake, typical_bed, len(wakes), _spread_minutes(wakes)


def confidence(nights: list[Night], today: date) -> float:
    """How much the forecast should be trusted, in [0.10, 0.85].

    Four things degrade it, and they are multiplicative because they
    compound: no data, no clock times, erratic wake times, stale data. A
    user with fourteen consistent logged nights gets the ceiling; a
    stranger gets the floor and the copy says so.

    `today` is required rather than read from the clock. Recency decay off
    the server's UTC date would age a user's data early — the same class of
    mistake as stamping their sleep log with a UTC day.
    """
    if not nights:
        return CONFIDENCE_FLOOR

    coverage = min(len(nights) / DEBT_WINDOW_NIGHTS, 1.0)

    _, _, phased, spread = wake_pattern(nights)
    phase_factor = 0.45 + 0.55 * min(phased / 7, 1.0)

    if phased >= 3:
        # 0 min spread -> 1.0; 2h spread -> 0.4 and falling
        consistency = max(0.35, min(1.0, 1.0 - spread / 200))
    else:
        consistency = 0.55

    newest = max(n.date for n in nights)
    try:
        age_days = max(0, (today - date.fromisoformat(newest)).days)
    except ValueError:
        age_days = 0
    recency = 1.0 if age_days <= 2 else max(0.4, 1.0 - (age_days - 2) / 21)

    value = CONFIDENCE_CEILING * coverage * phase_factor * consistency * recency
    return round(max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, value)), 2)


# ---------- projecting the curve forward ----------

def _projected_wakes(now: datetime, wake: time, horizon_h: int) -> list[datetime]:
    """Wake instants covering the horizon, plus the one before `now` so the
    current point knows how long the user has already been up."""
    first = datetime.combine(now.date() - timedelta(days=1), wake)
    out, cursor = [], first
    end = now + timedelta(hours=horizon_h)
    while cursor <= end + timedelta(days=1):
        out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _asleep(at: datetime, wake: time, bed: time) -> bool:
    minute = _minutes(at.time())
    bed_m, wake_m = _minutes(bed), _minutes(wake)
    if bed_m <= wake_m:                       # e.g. 01:00 -> 08:00, same day
        return bed_m <= minute < wake_m
    return minute >= bed_m or minute < wake_m  # straddles midnight, the usual case


def forecast(nights: list[Night], now_local: datetime,
             horizon_hours: int = 24, step_minutes: int = 30) -> Forecast:
    """The whole thing: history in, curve out.

    `now_local` is the caller's local time and is required. Defaulting it to
    the server clock would put every user in UTC and quietly shift their
    entire day — the kind of error that looks like a bad model rather than
    a bad timestamp.
    """
    horizon_hours = max(1, min(int(horizon_hours), 72))
    nights = list(nights or [])

    wake, bed, phased, _ = wake_pattern(nights)
    chronic, acute = sleep_debt(nights)
    penalty = debt_penalty(chronic, acute)
    wakes = _projected_wakes(now_local, wake, horizon_hours)

    points: list[Point] = []
    steps = int(horizon_hours * 60 / step_minutes) + 1
    for i in range(steps):
        at = now_local + timedelta(minutes=i * step_minutes)
        if _asleep(at, wake, bed):
            points.append(Point(at=at, hours_awake=None, energy=0.0,
                                asleep=True, drivers=["asleep"]))
            continue
        last_wake = max((w for w in wakes if w <= at), default=None)
        hours_awake = ((at - last_wake).total_seconds() / 3600
                       if last_wake else 0.0)
        points.append(Point(
            at=at,
            hours_awake=round(hours_awake, 2),
            energy=round(energy_at(hours_awake, penalty), 3),
            asleep=False,
            drivers=_drivers(hours_awake, penalty)))

    return Forecast(points=points,
                    confidence=confidence(nights, now_local.date()),
                    basis=_basis(nights, phased),
                    typical_wake=wake,
                    typical_bedtime=bed,
                    chronic_debt_h=chronic,
                    acute_deficit_h=acute)


def _basis(nights: list[Night], phased: int) -> str:
    """One sentence the agent can quote verbatim.

    Stating the basis is what keeps a low-confidence forecast honest rather
    than merely hedged — "typical patterns, no data from you yet" is a
    different claim from "your last 14 nights".
    """
    if not nights:
        return ("population averages — no sleep data yet, so this is a "
                "typical curve rather than yours")
    if phased == 0:
        return (f"{len(nights)} nights of duration data, but no bedtimes or "
                "wake times, so the timing is a population default and only "
                "the overall level is yours")
    return (f"{len(nights)} nights, {phased} with logged wake times")
