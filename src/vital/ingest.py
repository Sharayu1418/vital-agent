"""Health data ingestion: Apple Health XML export or plain CSV → one
normalized per-user sleep.csv the analysis sandbox can rely on.

Normalized schema (the ONLY schema analysis code ever sees — D6 applied
to the user's own data): date, duration_min, quality, source
"""
import csv
import io
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from vital.config import settings
from vital.storage import compute_duration_min

HEADER = ["date", "duration_min", "quality", "source"]


def parse_sleep_csv(content: bytes) -> list[dict]:
    """Accepts either (date, duration_min[, quality]) or
    (date, bedtime, wake_time[, quality]) — computes duration when needed."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if reader.fieldnames is None:
        raise ValueError("empty CSV")
    fields = {f.lower().strip(): f for f in reader.fieldnames}
    if "date" not in fields:
        raise ValueError("CSV needs a 'date' column")

    rows = []
    for raw in reader:
        get = lambda k: (raw.get(fields[k]) or "").strip() if k in fields else ""
        date_s = get("date")
        datetime.strptime(date_s, "%Y-%m-%d")  # validate, raises ValueError

        if get("duration_min"):
            duration = int(float(get("duration_min")))
        elif get("bedtime") and get("wake_time"):
            duration = compute_duration_min(get("bedtime"), get("wake_time"))
        else:
            raise ValueError(f"row {date_s}: need duration_min or bedtime+wake_time")
        if not 30 <= duration <= 18 * 60:
            raise ValueError(f"row {date_s}: implausible duration {duration} min")

        quality = get("quality")
        if quality and not 1 <= int(quality) <= 5:
            raise ValueError(f"row {date_s}: quality must be 1-5")
        rows.append({"date": date_s, "duration_min": duration,
                     "quality": quality, "source": "csv_upload"})
    if not rows:
        raise ValueError("no data rows found")
    return rows


def parse_apple_health_xml(content: bytes) -> list[dict]:
    """Apple Health export.xml → per-date asleep minutes.
    NOTE: real exports can be hundreds of MB; Phase 4 should switch to
    ET.iterparse streaming. Fine for typical sleep-only extracts now."""
    root = ET.fromstring(content)
    minutes_by_date: dict[str, float] = defaultdict(float)
    for rec in root.iter("Record"):
        if rec.get("type") != "HKCategoryTypeIdentifierSleepAnalysis":
            continue
        if "Asleep" not in (rec.get("value") or ""):
            continue  # skip InBed/Awake segments
        start = datetime.strptime(rec.get("startDate")[:19], "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(rec.get("endDate")[:19], "%Y-%m-%d %H:%M:%S")
        minutes_by_date[end.date().isoformat()] += (end - start).total_seconds() / 60

    rows = [{"date": d, "duration_min": int(m), "quality": "", "source": "apple_health"}
            for d, m in sorted(minutes_by_date.items()) if 30 <= m <= 18 * 60]
    if not rows:
        raise ValueError("no sleep records found in Apple Health export")
    return rows


def _user_dir(user_id: str) -> Path:
    d = Path(settings().data_dir) / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_sleep_data(user_id: str, rows: list[dict]) -> Path:
    """Merge-by-date with existing data (new upload wins on conflicts)."""
    path = _user_dir(user_id) / "sleep.csv"
    merged: dict[str, dict] = {}
    if path.exists():
        with path.open() as f:
            for row in csv.DictReader(f):
                merged[row["date"]] = row
    for row in rows:
        merged[row["date"]] = {k: str(v) for k, v in row.items()}
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows([merged[d] for d in sorted(merged)])
    return path


def user_sleep_csv(user_id: str) -> Path | None:
    path = Path(settings().data_dir) / user_id / "sleep.csv"
    return path if path.exists() else None


def csv_preview(path: Path, rows: int = 6) -> str:
    """First rows as text — injected into code-gen prompts so the model
    writes against columns that actually exist (Phase 2 pitfall #1)."""
    with path.open() as f:
        return "".join(line for _, line in zip(range(rows + 1), f))
