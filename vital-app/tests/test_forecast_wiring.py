"""The forecast's connections to the rest of the app.

Kept apart from test_forecast.py so the engine's own tests stay free of
package dependencies — the model is pure arithmetic and should be testable
as such.

What matters here is that the forecast is REACHABLE. An engine nothing
calls is the same as no engine, and the tool-wiring test in
test_people_connector.py exists because a previous version of that check
passed while the tool was unwired.
"""
import os
from datetime import date, datetime, time, timedelta, timezone

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import forecast as F
from vital import storage


# ---------- the user's clock, not the server's ----------

def test_the_offset_moves_the_local_day():
    """The bug this fixes: at 02:00 UTC it is still the previous evening in
    Albany, so a sleep log was being filed under tomorrow. Nothing surfaces
    that error — it just quietly corrupts sleep debt, which the forecast
    then presents as a confident curve."""
    token = storage.current_utc_offset_min.set(-300)      # UTC-5
    try:
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
        drift = abs((storage.local_now() - (utc_now - timedelta(minutes=300)))
                    .total_seconds())
        assert drift < 2, f"local_now drifted {drift}s from the offset"
    finally:
        storage.current_utc_offset_min.reset(token)


def test_an_absurd_offset_falls_back_to_utc():
    """A client bug or a probe must not be able to write a date years off."""
    for bad in ["99999", "-99999", "abc", None, ""]:
        assert storage.set_utc_offset(bad) == 0


def test_real_offsets_are_accepted():
    for good in ["-300", "0", "330", "840"]:      # US East, UTC, India, Kiribati
        assert storage.set_utc_offset(good) == int(good)
    storage.set_utc_offset(0)


def test_log_sleep_files_under_the_local_date(monkeypatch):
    """The regression guard for the date bug itself."""
    storage.set_utc_offset(-300)
    try:
        storage.log_sleep("23:00", "07:00", 4)
        rows = storage.sleep_history(1)
        assert rows[0]["log_date"] == storage.local_today().isoformat()
    finally:
        storage.set_utc_offset(0)


# ---------- the tool ----------

def _wired_tools(monkeypatch):
    """The tools ACTUALLY handed to create_react_agent — reading module
    imports would pass even with the tool unwired."""
    from vital.agents import sleep_energy
    captured = {}

    def fake_create(llm, tools=None, prompt=None):
        captured["names"] = [t.name for t in tools]
        return object()

    monkeypatch.setattr(sleep_energy, "create_react_agent", fake_create)
    monkeypatch.setattr(sleep_energy, "ChatVertexAI", lambda **kw: object())
    sleep_energy.build_agent()
    return captured["names"]


def test_forecast_energy_is_wired_into_the_sleep_agent(monkeypatch):
    pytest.importorskip("langchain_google_vertexai")
    assert "forecast_energy" in _wired_tools(monkeypatch)


def test_the_prompt_stops_restating_the_hardcoded_rule():
    """The prompt used to assert 'peak ~3-5h after wake' as a fact the model
    should repeat. That number is now computed from the user's own wake time
    and debt, so the prompt must defer to the tool rather than compete with
    it."""
    from vital.agents.sleep_energy import SYSTEM_PROMPT
    assert "forecast_energy" in SYSTEM_PROMPT
    assert "~3-5h after wake" not in SYSTEM_PROMPT


def test_the_summary_gives_the_model_clock_times_not_raw_points():
    """Handing over 49 points invites the model to do arithmetic on them,
    which it is bad at. Peak and dip are computed server-side."""
    from vital.agents.sleep_energy import summarize
    result = F.forecast(
        [F.Night(date="2026-08-0%d" % d, duration_min=420,
                 wake_time=time(7, 0), bedtime=time(23, 0)) for d in range(1, 8)],
        datetime(2026, 8, 5, 8, 0), horizon_hours=14)
    summary = summarize(result)
    assert summary["peak"]["at"] and summary["dip"]["at"]
    assert summary["peak"]["why"], "the planner quotes this"
    assert 0 <= summary["confidence"] <= 1
    assert summary["typical_wake"] == "07:00"


# ---------- the planner ----------

def test_the_planner_prompt_gains_the_forecast_rule(monkeypatch):
    from vital import planner
    monkeypatch.setattr(planner, "forecast_block", lambda: "\nENERGY FORECAST\n")

    captured = {}

    class FakeLLM:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, messages):
            captured["prompt"] = messages[0].content
            return planner.WeekPlan(items=[], tradeoffs="")

    planner.make_planner(FakeLLM())({"messages": [], "edit_request": None})
    assert "ENERGY FORECAST" in captured["prompt"]


def test_a_forecast_failure_does_not_stop_the_user_planning(monkeypatch):
    """Degradation policy: losing the forecast should cost the plan its
    energy justification, not block the weekend."""
    from vital import planner

    def boom(*a, **k):
        raise RuntimeError("db down")

    # patched on vital.storage, not planner.storage: the import inside
    # forecast_block() is function-local, so the module has no such attribute
    monkeypatch.setattr(storage, "sleep_history", boom)
    assert planner.forecast_block() == ""
