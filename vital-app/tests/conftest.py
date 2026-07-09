"""Global test fixtures.

Every test gets an isolated SQLite + data dir under tmp_path — tests must
NEVER touch the real vital.db or data/ (Phase 4 review finding: /chat's
budget check made even API tests hit the database)."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    from vital.config import settings
    settings.cache_clear()
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # pydantic-settings also reads the repo's real .env — a developer's
    # API_AUTH_TOKEN / DEBUG_ENDPOINTS / DATABASE_URL there must never
    # change test behavior. Env vars override env_file, so pin safe values.
    monkeypatch.setenv("API_AUTH_TOKEN", "")       # blank = not configured
    monkeypatch.setenv("DEBUG_ENDPOINTS", "false")
    monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "lax")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:3000")
    yield
    settings.cache_clear()
