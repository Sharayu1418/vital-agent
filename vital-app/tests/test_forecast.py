"""The energy forecast (A-1).

A forecast cannot be checked against reality on the day it is made. That
makes it the most dangerous kind of feature in this codebase: the Reddit
integration was dead for months because a graceful failure looked like a
blip, and a forecast that is simply WRONG looks exactly like a forecast
that is right. Nobody would notice for a very long time.

So the shape is pinned here rather than trusted. These tests re-derive the
peak, the dip, the rebound and the decline from the constants, so tuning a
constant and keeping the tests green means the shape survived — and
breaking them means it did not.
"""
import os
from datetime import date, datetime, time

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest

from vital import forecast as F

NOON = datetime(2026, 8, 5, 12, 0)
GRID = [i / 4 for i in range(0, 69)]          # 0 to 17h awake, 15-min steps


def nights(count, hours, wake="07:00", bed="23:00", start_day=1):
    return [F.Night(date=f"2026-08-{start_day + i:02d}",
                    duration_min=int(hours * 60),
                    wake_time=datetime.strptime(wake, "%H:%M").time(),
                    bedtime=datetime.strptime(bed, "%H:%M").time())
            for i in range(count)]


# ---------- the shape ----------

def test_energy_is_always_a_probability_like_number():
    """Unbounded output would let extreme debt produce negative energy, and
    the panel would render a curve below its own axis."""
    for h in [0, 0.5, 4, 8, 16, 24, 48, 100]:
        for penalty in [0.0, 0.2, 0.5, 5.0]:
            assert 0.0 <= F.energy_at(h, penalty) <= 1.0


def test_the_morning_peak_lands_three_to_five_hours_after_waking():
    """The claim the Sleep & Energy prompt has been making all along. It was
    untestable as prompt text; here it either holds or the build fails."""
    peak = max(((h, F.energy_at(h)) for h in GRID if h <= 6), key=lambda p: p[1])
    assert 3.0 <= peak[0] <= 5.0, f"peak at {peak[0]}h"


def test_the_morning_peak_is_the_high_point_of_the_whole_day():
    """Otherwise the planner would schedule the hardest thing in the evening
    — the opposite of the advice, and confidently justified."""
    peak = max(((h, F.energy_at(h)) for h in GRID if h <= 6), key=lambda p: p[1])
    assert peak[1] == max(F.energy_at(h) for h in GRID)


def test_the_afternoon_dip_lands_seven_to_nine_hours_after_waking():
    peak = max(((h, F.energy_at(h)) for h in GRID if h <= 6), key=lambda p: p[1])
    dip = min(((h, F.energy_at(h)) for h in GRID if peak[0] < h <= 12),
              key=lambda p: p[1])
    assert 7.0 <= dip[0] <= 9.0, f"dip at {dip[0]}h"
    assert peak[1] - dip[1] >= 0.08, "dip too shallow to be worth scheduling around"


def test_the_evening_recovers_but_never_beats_the_morning():
    """The second wind is real, and it is a partial recovery. If it exceeded
    the morning the model would recommend late-night effort."""
    peak = max(((h, F.energy_at(h)) for h in GRID if h <= 6), key=lambda p: p[1])
    dip = min(((h, F.energy_at(h)) for h in GRID if peak[0] < h <= 12),
              key=lambda p: p[1])
    rebound = max(((h, F.energy_at(h)) for h in GRID if dip[0] < h <= 15),
                  key=lambda p: p[1])
    assert dip[1] < rebound[1] < peak[1]


def test_waking_is_groggy():
    """Sleep inertia. Without it the curve starts at its maximum and the
    'morning peak' is not a peak at all."""
    assert F.energy_at(0) < F.energy_at(3) - 0.15


def test_energy_decays_deep_into_a_long_day():
    assert F.energy_at(20) < F.energy_at(12) < F.energy_at(4)


# ---------- debt ----------

def test_debt_lowers_the_whole_curve_not_just_part_of_it():
    for h in GRID:
        assert F.energy_at(h, 0.2) <= F.energy_at(h, 0.0)


def test_a_well_slept_fortnight_carries_no_debt():
    chronic, acute = F.sleep_debt(nights(14, 8.0))
    assert chronic == 0.0 and acute == 0.0


def test_short_nights_accumulate_debt():
    chronic, acute = F.sleep_debt(nights(5, 6.0))
    assert chronic == pytest.approx(10.0)      # 5 nights x 2h short
    assert acute == pytest.approx(2.0)


def test_sleeping_in_repays_debt():
    """Surplus has to count against deficit. Clamping each night at zero
    would make debt monotonically increasing, and the forecast would sink
    forever no matter how well the user slept."""
    # 3 nights 3h short (9h), then 3 nights 2h over (6h) -> 3h remaining
    mixed = nights(3, 5.0) + nights(3, 10.0, start_day=4)
    assert F.sleep_debt(mixed)[0] == pytest.approx(3.0)

    # enough catching up clears it entirely, and never goes negative:
    # a well-slept fortnight is not credit against a future bender
    recovered = nights(3, 5.0) + nights(4, 12.0, start_day=4)
    assert F.sleep_debt(recovered)[0] == 0.0


def test_the_debt_penalty_saturates():
    """A month of terrible sleep must not drive energy to zero, or the
    forecast stops telling 'tired' apart from 'catastrophic' exactly when
    the difference matters."""
    assert F.debt_penalty(500.0, 99.0) <= F.MAX_DEBT_PENALTY + F.ACUTE_PENALTY


def test_only_the_debt_window_counts():
    """Sleep from two months ago is not today's debt."""
    old = nights(40, 4.0)
    chronic, _ = F.sleep_debt(old)
    assert chronic <= F.DEBT_WINDOW_NIGHTS * 4 + 0.01


# ---------- clock handling ----------

def test_bedtimes_average_around_midnight_not_through_noon():
    """23:50 and 00:10 average to midnight. A naive mean gives 12:00 — the
    middle of the following day, and the worst possible answer for a model
    keyed to when the user sleeps."""
    assert F._circular_mean([time(23, 50), time(0, 10)]) == time(0, 0)


def test_a_normal_pair_averages_normally():
    assert F._circular_mean([time(7, 0), time(8, 0)]) == time(7, 30)


def test_sleep_windows_straddling_midnight_are_recognised():
    assert F._asleep(datetime(2026, 8, 5, 2, 0), time(7, 0), time(23, 0))
    assert not F._asleep(datetime(2026, 8, 5, 14, 0), time(7, 0), time(23, 0))


def test_sleep_windows_inside_one_day_are_recognised():
    """A 01:00 bedtime with an 08:00 wake never crosses midnight."""
    assert F._asleep(datetime(2026, 8, 5, 3, 0), time(8, 0), time(1, 0))
    assert not F._asleep(datetime(2026, 8, 5, 9, 0), time(8, 0), time(1, 0))


# ---------- confidence ----------

def test_no_data_means_the_floor():
    assert F.confidence([], date(2026, 8, 5)) == F.CONFIDENCE_FLOOR


def test_confidence_never_reaches_certainty():
    """v1 is a population model with population constants. Presenting it as
    certain would be the single most misleading thing this feature could
    do."""
    assert F.confidence(nights(60, 8.0), date(2026, 8, 15)) <= F.CONFIDENCE_CEILING


def test_duration_only_data_scores_far_below_logged_nights():
    """Apple Health exports carry no bedtime or wake time, so the timing of
    the curve is a population default even though the level is personal.
    The number has to say so."""
    upload_only = [F.Night(date=f"2026-08-{d:02d}", duration_min=450)
                   for d in range(1, 15)]
    assert (F.confidence(upload_only, date(2026, 8, 15))
            < F.confidence(nights(14, 7.5), date(2026, 8, 15)) * 0.7)


def test_erratic_wake_times_lower_confidence():
    steady = nights(14, 8.0, wake="07:00")
    erratic = [F.Night(date=f"2026-08-{d:02d}", duration_min=480,
                       wake_time=time(5 + (d % 7), 0), bedtime=time(23, 0))
               for d in range(1, 15)]
    assert F.confidence(erratic, date(2026, 8, 15)) < F.confidence(
        steady, date(2026, 8, 15))


def test_stale_data_lowers_confidence():
    fresh = nights(14, 8.0)
    assert (F.confidence(fresh, date(2026, 11, 1))
            < F.confidence(fresh, date(2026, 8, 15)))


def test_confidence_does_not_read_the_clock():
    """It takes `today`. Reading date.today() would use the server's UTC
    date and age a user's data early — the same class of mistake as filing
    their sleep log under a UTC day."""
    import inspect
    assert "today" in inspect.signature(F.confidence).parameters


# ---------- merging the two sources ----------

def test_manual_logs_win_over_uploads_on_the_same_date():
    """Only the manual row can carry phase, and the user typed it."""
    merged = F.nights_from_rows(
        [{"log_date": "2026-08-01", "duration_min": 400,
          "wake_time": "06:30", "bedtime": "23:00", "quality": 4}],
        [{"date": "2026-08-01", "duration_min": 999}])
    assert len(merged) == 1
    assert merged[0].duration_min == 400
    assert merged[0].wake_time == time(6, 30)


def test_uploaded_nights_still_count_toward_debt():
    """Dropping them because they lack clock times would throw away real
    information about how much the user actually slept."""
    merged = F.nights_from_rows([], [{"date": "2026-08-01", "duration_min": 300}])
    assert merged[0].duration_min == 300
    assert merged[0].wake_time is None
    assert F.sleep_debt(merged)[0] == pytest.approx(3.0)


def test_malformed_rows_are_dropped_not_fatal():
    """A single bad row must not take the forecast down; the panel and the
    planner both call this."""
    merged = F.nights_from_rows(
        [{"log_date": "", "duration_min": 400},
         {"log_date": "2026-08-02", "duration_min": None},
         {"log_date": "2026-08-03", "duration_min": "bad"},
         {"log_date": "2026-08-04", "duration_min": 480, "wake_time": "nonsense"}],
        [{"date": "2026-08-05"}])
    assert [n.date for n in merged] == ["2026-08-04"]
    assert merged[0].wake_time is None      # unparseable time degrades to None


# ---------- the whole forecast ----------

def test_the_forecast_is_anchored_to_the_callers_clock_not_the_servers():
    """The reason now_local is a required argument. Two callers an ocean
    apart must get different curves at the same instant."""
    history = nights(14, 8.0)
    morning = F.forecast(history, datetime(2026, 8, 5, 9, 0), horizon_hours=6)
    evening = F.forecast(history, datetime(2026, 8, 5, 21, 0), horizon_hours=6)
    assert morning.points[0].hours_awake != evening.points[0].hours_awake


def test_it_reports_a_peak_and_a_trough_in_local_clock_time():
    result = F.forecast(nights(14, 8.0), datetime(2026, 8, 5, 8, 0),
                        horizon_hours=14)
    peak, trough = result.peak(), result.trough()
    assert peak and trough and peak.energy > trough.energy
    assert peak.at.hour in range(9, 13), f"peak at {peak.at}"


def test_asleep_hours_are_marked_not_scored():
    """A curve that dips to zero overnight and a curve that says 'asleep'
    look identical on a chart but mean different things to the planner."""
    result = F.forecast(nights(14, 8.0, wake="07:00", bed="23:00"),
                        datetime(2026, 8, 5, 22, 0), horizon_hours=6)
    assert any(p.asleep for p in result.points)
    assert all(p.hours_awake is None for p in result.points if p.asleep)


def test_every_waking_point_can_explain_itself():
    """The planner quotes these. A forecast that cannot say why is
    indistinguishable from a number the model invented, which is the thing
    this feature exists to replace."""
    result = F.forecast(nights(14, 6.0), datetime(2026, 8, 5, 10, 0))
    assert all(p.drivers for p in result.waking_points())


def test_the_horizon_is_capped():
    """72h is the documented ceiling. An unbounded horizon would let one
    request build an arbitrarily long list."""
    long = F.forecast(nights(3, 8.0), NOON, horizon_hours=100_000)
    assert long.points[-1].at <= NOON.replace(day=8)


def test_no_history_still_produces_a_usable_curve():
    """A new user must get something, and must be told what it rests on."""
    result = F.forecast([], NOON)
    assert result.points and result.confidence == F.CONFIDENCE_FLOOR
    assert "population" in result.basis


def test_a_sleep_deprived_user_is_predicted_flatter_all_day():
    rested = F.forecast(nights(14, 8.0), datetime(2026, 8, 5, 8, 0), horizon_hours=12)
    wrecked = F.forecast(nights(14, 5.0), datetime(2026, 8, 5, 8, 0), horizon_hours=12)
    pairs = list(zip(rested.waking_points(), wrecked.waking_points()))
    assert pairs and all(r.energy >= w.energy for r, w in pairs)
    assert rested.peak().energy > wrecked.peak().energy + 0.1


def test_debt_shows_up_in_the_drivers():
    result = F.forecast(nights(14, 5.0), datetime(2026, 8, 5, 10, 0))
    assert any("sleep debt" in d
               for p in result.waking_points() for d in p.drivers)
