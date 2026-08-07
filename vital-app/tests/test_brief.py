"""The morning brief.

A daily notification gets exactly one chance. The moment it feels like
noise it is turned off and never turned back on — so the constraints that
keep it welcome are enforced here rather than left to whoever edits the
copy next.

Two of these tests protect properties that fail silently and would never be
reported as bugs: sending twice, and sending filler.
"""
import os
from datetime import datetime, time, timedelta

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import brief, brief_job
from vital import forecast as engine

MORNING = datetime(2026, 8, 6, 7, 0)


def nights(count, hours, wake="07:00", bed="23:00"):
    return [engine.Night(date=f"2026-08-{d:02d}", duration_min=int(hours * 60),
                         wake_time=datetime.strptime(wake, "%H:%M").time(),
                         bedtime=datetime.strptime(bed, "%H:%M").time())
            for d in range(1, count + 1)]


def tired():
    history = nights(6, 6.2, bed="01:00")
    return history, engine.forecast(history, MORNING, horizon_hours=18)


# ---------- the content rules ----------

def test_a_brief_stays_under_the_word_cap():
    """Past this it needs scrolling on a lock screen, which means it is not
    read at all."""
    history, forecast = tired()
    result = brief.compose(MORNING, history, forecast, [], name="Sharayu")
    assert result and result.word_count() <= brief.MAX_WORDS


def test_it_gives_exactly_one_adjustment():
    """A list of three things arriving at 7am is a to-do list, and a to-do
    list is what people mute."""
    history, forecast = tired()
    dip = brief.daytime_dip(forecast)
    events = [
        {"start": f"{dip.at.hour:02d}:00", "title": "Deep work", "kind": "idea_work"},
        {"start": f"{dip.at.hour:02d}:30", "title": "Climbing", "kind": "activity"},
    ]
    body = brief.compose(MORNING, history, forecast, events).body
    assert body.count("—") <= 1, "more than one adjustment made it in"


def test_nothing_worth_saying_produces_nothing():
    """Silence is a supported outcome. A notification padded with filler
    trains people to ignore the next one, and the next one might matter."""
    rested = nights(14, 8.2)
    forecast = engine.forecast(rested, MORNING, horizon_hours=18)
    assert brief.compose(MORNING, rested, forecast,
                         [{"start": "10:00", "title": "Climbing",
                           "kind": "activity"}]) is None


def test_no_data_at_all_produces_nothing():
    assert brief.compose(MORNING, [], None, []) is None


def test_it_never_guilts():
    """The app's whole tone is 'you don't have to earn your evening'. A
    notification is the easiest place to accidentally contradict that."""
    history, forecast = tired()
    body = brief.compose(MORNING, history, forecast, []).body.lower()
    for word in ["should", "failed", "missed", "streak", "behind",
                 "only got", "need to", "must"]:
        assert word not in body, f"{word!r} is guilt, not information"


def test_a_low_confidence_forecast_does_not_claim_a_peak():
    """Waking someone to tell them a population average is worse than not
    waking them."""
    thin = nights(1, 7.0)
    forecast = engine.forecast(thin, MORNING, horizon_hours=18)
    result = brief.compose(MORNING, thin, forecast, [])
    if result:
        assert "sharpest" not in result.body.lower()


def test_the_dip_it_reports_is_the_afternoon_one():
    """forecast.trough() is the global minimum, which over a morning-to-bed
    horizon is always the hour before sleep. Telling somebody their energy
    bottoms out at 00:30 is true, useless, and reads like chart-reading."""
    _, forecast = tired()
    dip = brief.daytime_dip(forecast)
    assert dip is not None and 12 <= dip.at.hour < 20, f"dip at {dip.at}"
    assert forecast.trough().at.hour != dip.at.hour, "guard is not doing anything"


def test_a_conflict_with_the_dip_is_what_gets_flagged():
    history, forecast = tired()
    dip = brief.daytime_dip(forecast)
    body = brief.compose(MORNING, history, forecast,
                         [{"start": f"{dip.at.hour:02d}:00",
                           "title": "Deep work", "kind": "idea_work"}]).body
    assert "Deep work" in body and "dip" in body


def test_sleep_debt_is_reported_as_a_number_not_an_alarm():
    history, forecast = tired()
    body = brief.compose(MORNING, history, forecast, []).body
    assert "h of sleep debt" in body
    assert "!" not in body


# ---------- the schedule ----------

def _prefs(**over):
    base = {"user_id": "u1", "brief_enabled": 1, "brief_hour": 7,
            "utc_offset_min": -300, "last_brief_date": None,
            "display_name": "Sharayu"}
    return {**base, **over}


def test_it_fires_at_the_users_local_hour_not_utc():
    """'7am' is twenty-four different instants. Sending at 07:00 UTC is
    03:00 in New York — the single most likely way to make this hated."""
    noon_utc = datetime(2026, 8, 6, 12, 0)      # 07:00 in UTC-5
    assert brief_job.due(_prefs(), noon_utc)
    assert not brief_job.due(_prefs(), datetime(2026, 8, 6, 7, 0))


def test_it_will_not_send_twice_in_one_day():
    """Cloud Scheduler retries on timeout and can fire twice. Nothing
    erodes trust faster than the same notification arriving again."""
    noon_utc = datetime(2026, 8, 6, 12, 0)
    already = _prefs(last_brief_date="2026-08-06")
    assert not brief_job.due(already, noon_utc)


def test_yesterdays_send_does_not_block_today():
    noon_utc = datetime(2026, 8, 6, 12, 0)
    assert brief_job.due(_prefs(last_brief_date="2026-08-05"), noon_utc)


def test_the_day_boundary_is_the_users_not_the_servers():
    """At 02:00 UTC it is still the previous evening in New York. Using the
    server's date would let a second brief through."""
    late_utc = datetime(2026, 8, 7, 2, 0)       # 21:00 on the 6th, UTC-5
    prefs = _prefs(brief_hour=21, last_brief_date="2026-08-06")
    assert not brief_job.due(prefs, late_utc)


def test_disabled_users_are_never_due():
    assert not brief_job.due(_prefs(brief_enabled=0),
                             datetime(2026, 8, 6, 12, 0))


def test_a_missing_offset_defaults_to_utc_rather_than_crashing():
    prefs = _prefs(utc_offset_min=None)
    assert brief_job.due(prefs, datetime(2026, 8, 6, 7, 0))


# ---------- the job endpoint is not public ----------

def test_the_job_endpoint_rejects_callers_without_the_token(monkeypatch):
    """A URL that makes the app notify every one of its users must never be
    open — it is also a free way to drain the push quota."""
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient

    import vital.api as api
    from vital.config import settings

    monkeypatch.setenv("BRIEF_JOB_TOKEN", "the-real-token")
    settings.cache_clear()
    try:
        client = TestClient(api.app)
        assert client.post("/jobs/morning-brief").status_code == 401
        assert client.post("/jobs/morning-brief",
                           headers={"X-Job-Token": "guess"}).status_code == 401
    finally:
        settings.cache_clear()


def test_the_job_endpoint_refuses_when_unconfigured(monkeypatch):
    """Fail closed. An empty configured token must not mean 'let everyone
    in' — the classic comparison-against-empty-string hole."""
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient

    import vital.api as api
    from vital.config import settings

    monkeypatch.setenv("BRIEF_JOB_TOKEN", "")
    settings.cache_clear()
    try:
        client = TestClient(api.app)
        for headers in ({}, {"X-Job-Token": ""}, {"X-Job-Token": "anything"}):
            assert client.post("/jobs/morning-brief",
                               headers=headers).status_code == 503
    finally:
        settings.cache_clear()
