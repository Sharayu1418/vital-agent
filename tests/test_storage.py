"""Storage validation tests — sleep input is validated in code, not by the LLM."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest

from vital import storage
from vital.storage import compute_duration_min


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    from vital.config import settings
    settings.cache_clear()
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    yield
    settings.cache_clear()


def test_duration_crosses_midnight():
    assert compute_duration_min("23:30", "07:00") == 450


def test_duration_same_side_of_midnight():
    assert compute_duration_min("01:00", "08:30") == 450


def test_log_sleep_computes_and_stores_duration():
    storage.current_user_id.set("u1")
    assert storage.log_sleep("23:00", "07:00", 4) == 480
    assert storage.sleep_history(1)[0]["duration_min"] == 480


@pytest.mark.parametrize("bed,wake,quality", [
    ("23:00", "07:00", 0),      # quality out of range
    ("23:00", "07:00", 9),
    ("25:00", "07:00", 3),      # invalid hour
    ("11pm", "7am", 3),         # wrong format
    ("23:00", "23:10", 3),      # 10 min sleep: implausible
])
def test_log_sleep_rejects_bad_input(bed, wake, quality):
    storage.current_user_id.set("u1")
    with pytest.raises(ValueError):
        storage.log_sleep(bed, wake, quality)


def test_user_isolation():
    storage.current_user_id.set("alice")
    storage.log_sleep("23:00", "07:00", 4)
    storage.current_user_id.set("bob")
    assert storage.sleep_history(14) == []
