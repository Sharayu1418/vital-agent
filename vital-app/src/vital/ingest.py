"""Health data ingestion: Apple Health XML export or plain CSV → normalized
per-user sleep rows in the shared app store (Postgres in prod, SQLite in
dev — container disk is ephemeral on Cloud Run, so files are out).

Normalized schema (the ONLY schema analysis code ever sees — D6 applied
to the user's own data): date, duration_min, quality, source

The analysis sandbox still consumes CSV bytes; sleep_csv_bytes() renders
them from the database on demand.
"""
import csv
import io
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import datetime

from vital import storage
from vital.storage import compute_duration_min

HEADER = ["date", "duration_min", "quality", "source"]

# Apple's share sheet produces export.zip; the XML inside is the real payload.
APPLE_XML_MEMBERS = ("apple_health_export/export.xml", "export.xml")


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
        def get(k: str) -> str:
            return (raw.get(fields[k]) or "").strip() if k in fields else ""

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


def parse_apple_health_stream(fileobj) -> list[dict]:
    """Apple Health export.xml → per-date asleep minutes, STREAMING.

    Real exports are routinely 100MB-1GB. The previous implementation called
    ET.fromstring() on the whole document, which builds the entire tree in
    memory (several times the file size) and OOM-killed the container before
    it could answer.

    iterparse walks the document incrementally and we clear each <Record>
    the moment it's consumed, including the accumulated siblings the parser
    keeps on the root. Peak memory becomes the size of the per-date tally,
    not the size of the file.

    Accepts any binary file-like object, so callers can hand it a spooled
    temp file rather than a bytes blob.
    """
    minutes_by_date: dict[str, float] = defaultdict(float)
    root = None
    for event, elem in ET.iterparse(fileobj, events=("start", "end")):
        if event == "start":
            if root is None:
                root = elem           # first element: the document root
            continue
        if elem.tag != "Record":
            continue
        try:
            if (elem.get("type") == "HKCategoryTypeIdentifierSleepAnalysis"
                    and "Asleep" in (elem.get("value") or "")):
                start = datetime.strptime(elem.get("startDate")[:19], "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(elem.get("endDate")[:19], "%Y-%m-%d %H:%M:%S")
                minutes_by_date[end.date().isoformat()] += (
                    end - start).total_seconds() / 60
        except (TypeError, ValueError):
            pass  # one malformed record must not sink a multi-year export
        finally:
            # drop this record AND the root's reference to it, or the parser
            # quietly retains every sibling and we're back to O(filesize)
            elem.clear()
            if root is not None:
                root.clear()

    rows = [{"date": d, "duration_min": int(m), "quality": "", "source": "apple_health"}
            for d, m in sorted(minutes_by_date.items()) if 30 <= m <= 18 * 60]
    if not rows:
        raise ValueError("no sleep records found in Apple Health export")
    return rows


def parse_apple_health_xml(content: bytes) -> list[dict]:
    """Bytes convenience wrapper over parse_apple_health_stream."""
    return parse_apple_health_stream(io.BytesIO(content))


def parse_apple_health_zip(fileobj) -> list[dict]:
    """Apple's share sheet exports export.zip, not a bare XML file — so
    users previously had to unzip a 400MB archive by hand before uploading.
    Reads the XML member as a stream; the archive is never fully expanded."""
    with zipfile.ZipFile(fileobj) as archive:
        name = next((n for n in APPLE_XML_MEMBERS if n in archive.namelist()), None)
        if name is None:
            name = next((n for n in archive.namelist()
                         if n.endswith("export.xml")), None)
        if name is None:
            raise ValueError("zip does not contain an Apple Health export.xml")
        with archive.open(name) as member:
            return parse_apple_health_stream(member)


def save_sleep_data(user_id: str, rows: list[dict]) -> int:
    """Merge-by-date with existing data (new upload wins on conflicts).
    Returns the number of rows written."""
    return storage.save_health_rows(user_id, rows)


def sleep_csv_bytes(user_id: str) -> bytes | None:
    """The user's uploaded sleep data rendered as normalized CSV bytes
    (what the analysis sandbox consumes), or None if nothing is uploaded."""
    rows = storage.health_rows(user_id)
    if not rows:
        return None
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=HEADER)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


def csv_preview(data: bytes, rows: int = 6) -> str:
    """First rows as text — injected into code-gen prompts so the model
    writes against columns that actually exist (Phase 2 pitfall #1)."""
    lines = data.decode().splitlines(keepends=True)
    return "".join(lines[:rows + 1])
