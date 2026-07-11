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

    # Storage (D2): one relational store for everything. DATABASE_URL set →
    # Postgres carries checkpoints, memories, AND all app tables (sleep,
    # uploads, feedback, calendar, buddies, usage). Unset → SQLite locally.
    sqlite_path: str = "vital.db"
    database_url: str | None = None

    # --- Firebase Authentication (Google Sign-In) ---
    # OFF by default: local dev and tests stay zero-network. When enabled,
    # bearer tokens that aren't the internal API_AUTH_TOKEN are verified as
    # Firebase ID tokens via the Admin SDK using ADC (never a JSON key file).
    firebase_auth_enabled: bool = False
    firebase_project_id: str | None = None

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
    # 'lax' works same-site (localhost dev; app./api. subdomains in prod).
    # Vercel <-> Cloud Run on different sites needs 'none' — which activates
    # the origin-check CSRF guard and REQUIRES secure=true (startup-enforced).
    session_cookie_samesite: str = "lax"  # lax | none | strict

    # --- Phase 2: sandbox + memory ---
    e2b_api_key: str | None = None       # from e2b.dev; free tier for dev
    sandbox_timeout_seconds: float = 30.0
    max_repair_attempts: int = 3
    # uploaded health data lives in the shared relational store (storage.py);
    # container disk is ephemeral on Cloud Run, so no DATA_DIR anymore
    memory_recall_limit: int = 5

    # --- Phase 3: events provider (free key: developer.ticketmaster.com) ---
    ticketmaster_api_key: str | None = None

    # --- Phase 4: guardrails ---
    daily_token_budget: int = 50_000   # per user; ~$0.05/day at Flash prices
    recursion_limit: int = 25          # hard cap on graph steps per turn

    # --- Phase 5: frontend origin for CORS (Vercel URL in prod) ---
    frontend_origin: str = "http://localhost:3000"


@lru_cache
def settings() -> Settings:
    return Settings()
