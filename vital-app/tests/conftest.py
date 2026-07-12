"""Global test fixtures.

Every test gets an isolated SQLite + data dir under tmp_path — tests must
NEVER touch the real vital.db or data/ (Phase 4 review finding: /chat's
budget check made even API tests hit the database)."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

# A developer's real .env must never leak into tests. pydantic-settings
# falls back to the env FILE only when a variable is absent from the
# environment — so pin these HERE, at import time, before any module-level
# settings() call (api.py registers /debug routes at import).
os.environ["API_AUTH_TOKEN"] = ""
os.environ["DEBUG_ENDPOINTS"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ["TICKETMASTER_API_KEY"] = ""
os.environ["AUTH_REQUIRED"] = "false"  # anonymous access in tests by default

import pytest


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    from vital.config import settings
    settings.cache_clear()
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
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
