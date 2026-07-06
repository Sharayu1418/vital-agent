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


@lru_cache
def settings() -> Settings:
    return Settings()
