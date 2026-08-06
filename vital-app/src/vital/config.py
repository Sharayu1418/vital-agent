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
    # OAuth-first: when true, user-data routes reject unauthenticated
    # callers with 401 instead of minting anonymous session identities.
    # False locally (tests run offline, anonymous dev still works);
    # TRUE in production. Public routes (/healthz, /docs) stay public.
    auth_required: bool = False

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
    # P1-6: messages sent to an agent per turn. The whole thread used to go
    # every time, so cost and latency grew with conversation length and a
    # long enough thread would eventually blow the context window mid-chat.
    # Durable facts live in long-term memory and are injected separately, so
    # older turns are the cheapest thing to drop.
    history_limit: int = 20

    # --- Semantic memory (pgvector via LangGraph's store index) ---
    # Retrieval and dedup were keyword-based and both failed the same way:
    # "User is in Albany", "User is located in or near Albany" and "User is
    # located in Albany/Guilderland" are three rows for one fact, and
    # "ceramics" never retrieved a stored "pottery" fact.
    embedding_model: str = "text-embedding-004"
    embedding_dims: int = 768          # text-embedding-004 output size
    # Cosine similarity above which a new fact OVERWRITES an existing one.
    #
    # Compared on ONE scale, everywhere: memory.similarity() embeds both
    # facts as documents. That is not the default behaviour and the number
    # is meaningless without it — dedup used to inherit whatever scale the
    # store backend scored on, and the two backends disagree:
    #
    #   InMemoryStore (tests, live eval)  search query -> embed_query
    #   PostgresStore 3.1.0 (production)  search query -> embed_documents
    #
    # text-embedding-004 is task-typed, so the same pair scores ~0.24 lower
    # on the query path. Three thresholds shipped before anyone noticed:
    # 0.82 and 0.87 were calibrated doc-doc and enforced doc-query so dedup
    # never fired; 0.63 was calibrated doc-query and enforced doc-doc in
    # production, where it merged every distinct fact into a single row.
    # duplicate_key() now re-scores candidates itself and ignores the
    # store's score entirely.
    #
    # MEASURED symmetrically — scripts/tune_memory_threshold.py, 5 Aug 2026:
    #
    #   should merge   min : 0.930
    #   must not merge max : 0.800
    #
    # 0.87 sits mid-gap. test_memory_live.py::
    # test_the_threshold_still_sits_between_the_bands re-measures and fails
    # if that stops holding. The failure directions are NOT symmetric: too
    # high leaves duplicates, too low silently eats distinct memories.
    memory_dedup_threshold: float = 0.87

    # --- Phase 3: events provider (free key: developer.ticketmaster.com) ---
    ticketmaster_api_key: str | None = None

    # --- Wearable sync: Google Health API (formerly the Fitbit Web API) ---
    # The Fitbit Web API is decommissioned in September 2026 and no OAuth
    # token carries over, so there is no version of this that builds on it.
    #
    # The client ID is public by design (it ships in a redirect URL). The
    # SECRET must come from Secret Manager, never from Vercel or a NEXT_
    # PUBLIC_ variable.
    google_health_client_id: str | None = None
    google_health_client_secret: str | None = None
    # Where Google sends the user back. Must match the Authorized redirect
    # URI on the OAuth client exactly, including scheme and trailing path.
    google_health_redirect_uri: str | None = None
    # Fernet key protecting stored refresh tokens (see secrets.py). Without
    # it the connect route refuses rather than storing bearer credentials to
    # a third party's health data in plaintext.
    token_encryption_key: str | None = None
    # How stale synced sleep data may be before /forecast refreshes it.
    # Wearables post last night's sleep once, in the morning; polling harder
    # spends rate limit for nothing.
    sync_max_age_minutes: int = 180

    # NOTE: there is deliberately no community-search provider here. Every
    # third-party community API closed between 2019 and 2026 (Reddit,
    # Meetup, Facebook Groups, Eventbrite search, Strava clubs). Community
    # discovery is served by Google Places + the Activity Buddy Board
    # instead — see docs/LIMITATIONS.md before adding another one.

    # --- Phase 4: guardrails ---
    daily_token_budget: int = 50_000   # per user; ~$0.05/day at Flash prices
    # Hard ceiling on the crisis screen's model call. Past this we stop
    # waiting and fall back to deterministic matching — a distressed person
    # must never sit on a spinner because Vertex is slow.
    crisis_timeout_seconds: float = 4.0
    recursion_limit: int = 25          # hard cap on graph steps per turn

    # --- Phase 5: frontend origin for CORS (Vercel URL in prod) ---
    frontend_origin: str = "http://localhost:3000"
    # Optional anchored regex for additional allowed origins — in practice
    # Vercel preview deployments, which get a fresh subdomain per branch and
    # are otherwise blocked by the single-origin allowlist above (P1-12).
    #
    # OPT-IN ON PURPOSE. It would be easy to derive this from
    # frontend_origin ("vital-agent.vercel.app" -> "vital-agent-*.vercel.app")
    # but that would be a security bug: *.vercel.app is a shared namespace,
    # so anyone can register a project whose subdomain matches the wildcard.
    # Combined with allow_credentials=True, that would hand a stranger's
    # site the ability to make authenticated requests as your users. Write
    # the pattern deliberately, anchored, and keep it as tight as possible.
    #
    #   PREVIEW_ORIGIN_REGEX=^https://vital-agent-git-[a-z0-9-]+-myteam\.vercel\.app$
    preview_origin_regex: str | None = None


@lru_cache
def settings() -> Settings:
    return Settings()
