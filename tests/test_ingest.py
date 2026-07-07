"""Ingestion tests: CSV variants, Apple Health XML, merge-by-date."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest

from vital import ingest


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    from vital.config import settings
    settings.cache_clear()
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    yield
    settings.cache_clear()


def test_csv_with_duration():
    rows = ingest.parse_sleep_csv(b"date,duration_min,quality\n2026-07-01,420,3\n")
    assert rows[0] == {"date": "2026-07-01", "duration_min": 420,
                       "quality": "3", "source": "csv_upload"}


def test_csv_with_bedtime_wake_computes_duration():
    rows = ingest.parse_sleep_csv(b"date,bedtime,wake_time\n2026-07-01,23:30,07:00\n")
    assert rows[0]["duration_min"] == 450


@pytest.mark.parametrize("content,fragment", [
    (b"", "empty CSV"),
    (b"foo,bar\n1,2\n", "'date' column"),
    (b"date,duration_min\nnot-a-date,400\n", ""),          # strptime ValueError
    (b"date,duration_min\n2026-07-01,10\n", "implausible"),
    (b"date,duration_min,quality\n2026-07-01,400,9\n", "quality"),
    (b"date\n2026-07-01\n", "duration_min or bedtime"),
])
def test_bad_csv_rejected(content, fragment):
    with pytest.raises(ValueError) as exc:
        ingest.parse_sleep_csv(content)
    assert fragment in str(exc.value)


APPLE_XML = b"""<?xml version="1.0"?>
<HealthData>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis"
         value="HKCategoryValueSleepAnalysisAsleepCore"
         startDate="2026-07-01 23:30:00 -0400" endDate="2026-07-02 03:30:00 -0400"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis"
         value="HKCategoryValueSleepAnalysisAsleepDeep"
         startDate="2026-07-02 03:30:00 -0400" endDate="2026-07-02 06:30:00 -0400"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis"
         value="HKCategoryValueSleepAnalysisInBed"
         startDate="2026-07-01 23:00:00 -0400" endDate="2026-07-02 07:00:00 -0400"/>
 <Record type="HKQuantityTypeIdentifierStepCount" value="9000"
         startDate="2026-07-02 09:00:00 -0400" endDate="2026-07-02 10:00:00 -0400"/>
</HealthData>"""


def test_apple_health_sums_asleep_segments_ignores_inbed_and_steps():
    rows = ingest.parse_apple_health_xml(APPLE_XML)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-07-02"
    assert rows[0]["duration_min"] == 420  # 240 + 180, InBed excluded
    assert rows[0]["source"] == "apple_health"


def test_apple_health_with_no_sleep_records_raises():
    with pytest.raises(ValueError):
        ingest.parse_apple_health_xml(b"<HealthData></HealthData>")


def test_save_merges_by_date_new_wins():
    ingest.save_sleep_data("u1", [{"date": "2026-07-01", "duration_min": 400,
                                   "quality": "", "source": "csv_upload"}])
    path = ingest.save_sleep_data("u1", [
        {"date": "2026-07-01", "duration_min": 410, "quality": "", "source": "apple_health"},
        {"date": "2026-07-02", "duration_min": 380, "quality": "", "source": "apple_health"},
    ])
    content = path.read_text()
    assert content.count("2026-07-01") == 1     # merged, not duplicated
    assert "410" in content and "400" not in content.split("\n")[1]
    preview = ingest.csv_preview(path)
    assert preview.startswith("date,duration_min")


def test_user_sleep_csv_none_when_no_upload():
    assert ingest.user_sleep_csv("nobody") is None
