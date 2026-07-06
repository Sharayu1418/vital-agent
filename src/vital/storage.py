"""App data store. SQLite for local dev; same SQL moves to Cloud SQL Postgres
in Phase 2 (D2: one relational store for everything).

user_id flows via a contextvar set per-request in api.py — tools are called
by the LLM, which must never see or choose user identity.
"""
import sqlite3
from contextvars import ContextVar
from datetime import date
from pathlib import Path

from vital.config import settings

current_user_id: ContextVar[str] = ContextVar("current_user_id", default="local-user")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sleep_logs (
    user_id TEXT, log_date TEXT, bedtime TEXT, wake_time TEXT,
    duration_min INTEGER, quality INTEGER,
    PRIMARY KEY (user_id, log_date)
);
CREATE TABLE IF NOT EXISTS interests (
    user_id TEXT, interest TEXT, PRIMARY KEY (user_id, interest)
);
CREATE TABLE IF NOT EXISTS ideas (
    user_id TEXT, idea TEXT, category TEXT, created TEXT
);
"""


def _conn() -> sqlite3.Connection:
    path = Path(settings().sqlite_path)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def compute_duration_min(bedtime: str, wake_time: str) -> int:
    """Duration from 'HH:MM' strings, handling the cross-midnight case.
    Computed HERE, not by the LLM — models are unreliable at time arithmetic."""
    from datetime import datetime, timedelta
    bed = datetime.strptime(bedtime, "%H:%M")
    wake = datetime.strptime(wake_time, "%H:%M")
    if wake <= bed:
        wake += timedelta(days=1)
    return int((wake - bed).total_seconds() // 60)


def log_sleep(bedtime: str, wake_time: str, quality: int) -> int:
    """Validates and stores; returns computed duration_min.
    Raises ValueError on impossible input — the tool layer turns that into
    a message the model can use to re-ask the user."""
    if not 1 <= quality <= 5:
        raise ValueError(f"quality must be 1-5, got {quality}")
    duration_min = compute_duration_min(bedtime, wake_time)  # raises on bad HH:MM
    if not 30 <= duration_min <= 18 * 60:
        raise ValueError(f"implausible sleep duration: {duration_min} min")
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO sleep_logs VALUES (?, ?, ?, ?, ?, ?)",
            (current_user_id.get(), date.today().isoformat(), bedtime, wake_time,
             duration_min, quality),
        )
    return duration_min


def sleep_history(days: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM sleep_logs WHERE user_id = ? ORDER BY log_date DESC LIMIT ?",
            (current_user_id.get(), days),
        ).fetchall()
    return [dict(r) for r in rows]


def add_interest(interest: str) -> None:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO interests VALUES (?, ?)",
                  (current_user_id.get(), interest.lower()))


def interests() -> list[str]:
    with _conn() as c:
        rows = c.execute("SELECT interest FROM interests WHERE user_id = ?",
                         (current_user_id.get(),)).fetchall()
    return [r["interest"] for r in rows]


def save_idea(idea: str, category: str) -> None:
    with _conn() as c:
        c.execute("INSERT INTO ideas VALUES (?, ?, ?, ?)",
                  (current_user_id.get(), idea, category, date.today().isoformat()))


def saved_ideas() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT idea, category, created FROM ideas WHERE user_id = ?",
                         (current_user_id.get(),)).fetchall()
    return [dict(r) for r in rows]
