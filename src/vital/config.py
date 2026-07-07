"""Central config. One place for every knob (12-factor)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_cloud_project: str
    google_cloud_location: str = "us-east1"

    # D5: Flash by default. Changing models is a config change, not a code change.
    vital_model: str = "gemini-2.5-flash"

    openweather_api_key: str
    google_places_api_key: str

    # Tool adapter timeouts — external APIs must never hang the agent loop
    tool_timeout_seconds: float = 8.0

    # Storage (D2): SQLite locally; set DATABASE_URL for Postgres checkpoints
    sqlite_path: str = "vital.db"
    database_url: str | None = None

    # --- Security (safe-by-default: both OFF unless explicitly set) ---
    # Shared bearer token for trusted callers (your frontend's backend).
    # Only token-authenticated callers may assert a user_id; everyone else
    # is pinned to 'local-user'. Replaced by real per-user auth in Phase 5.
    api_auth_token: str | None = None
    # /debug/* routes exist only when true. NEVER true on a public deploy.
    debug_endpoints: bool = False
    # Session cookie Secure flag. Default TRUE (fail-safe for prod, where
    # Cloud Run terminates TLS); local dev over http sets false in .env.
    session_cookie_secure: bool = True

    # --- Phase 2: sandbox + memory ---
    e2b_api_key: str | None = None       # from e2b.dev; free tier for dev
    sandbox_timeout_seconds: float = 30.0
    max_repair_attempts: int = 3
    data_dir: str = "data"               # per-user uploaded health data (GCS in prod)
    memory_recall_limit: int = 5


@lru_cache
def settings() -> Settings:
    return Settings()
