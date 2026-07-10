"""Ingestion tests: CSV variants, Apple Health XML, merge-by-date.
Uploaded rows live in the shared app store (conftest gives each test an
isolated SQLite database), not on disk."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest

from vital import ingest


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
    ingest.save_sleep_data("u1", [
        {"date": "2026-07-01", "duration_min": 410, "quality": "", "source": "apple_health"},
        {"date": "2026-07-02", "duration_min": 380, "quality": "", "source": "apple_health"},
    ])
    content = ingest.sleep_csv_bytes("u1").decode()
    assert content.count("2026-07-01") == 1     # merged, not duplicated
    assert "410" in content and "400" not in content.split("\n")[1]
    preview = ingest.csv_preview(ingest.sleep_csv_bytes("u1"))
    assert preview.startswith("date,duration_min")


def test_sleep_data_is_user_isolated():
    ingest.save_sleep_data("u1", [{"date": "2026-07-01", "duration_min": 400,
                                   "quality": "", "source": "csv_upload"}])
    assert ingest.sleep_csv_bytes("u2") is None


def test_sleep_csv_bytes_none_when_no_upload():
    assert ingest.sleep_csv_bytes("nobody") is None


def test_csv_preview_caps_rows():
    rows = [{"date": f"2026-07-{d:02d}", "duration_min": 400 + d,
             "quality": "", "source": "csv_upload"} for d in range(1, 11)]
    ingest.save_sleep_data("u1", rows)
    preview = ingest.csv_preview(ingest.sleep_csv_bytes("u1"), rows=3)
    assert len(preview.splitlines()) == 4       # header + 3 data rows
