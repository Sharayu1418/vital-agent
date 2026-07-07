# Every account + key VITAL needs (and when)

## Required to run the core app

| Account | What for | Key/setup | Cost |
|---|---|---|---|
| **Google Cloud** | Vertex AI (Gemini), Cloud Run deploy, later Cloud SQL | No API key — `gcloud auth application-default login` locally; service identity on Cloud Run. Enable `aiplatform.googleapis.com`, `run.googleapis.com`, `secretmanager.googleapis.com` | New accounts: $300 credit. Flash usage here: cents/day |
| **OpenWeather** | weather tool (indoor/outdoor decisions) | openweathermap.org → API keys → `OPENWEATHER_API_KEY` | Free tier (1000 calls/day) |
| **Google Places** | venue search (Activity Scout) | Same GCP project → enable Places API (New) → create API key → `GOOGLE_PLACES_API_KEY`. Requires billing enabled | $200/mo free usage credit |
| **E2B** | sandboxed pandas analysis of sleep data | e2b.dev → dashboard → `E2B_API_KEY` | Free tier fine for dev |

## Optional (features degrade gracefully without them)

| Account | What for | Key | Cost |
|---|---|---|---|
| **Ticketmaster Developer** | events tool (People Connector) | developer.ticketmaster.com → `TICKETMASTER_API_KEY` | Free (5000 calls/day) |
| **LangSmith** | tracing every agent hop, eval datasets | smith.langchain.com → `LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true` | Free dev tier |
| Reddit | communities tool | none — public JSON endpoint | free |

## Ship/publish accounts (Phase 5)

| Account | What for | Notes |
|---|---|---|
| **GitHub** | repo, CI, nightly eval secrets | Secrets: `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `GCP_PROJECT_ID` |
| **Vercel** | frontend hosting | free hobby tier; set `NEXT_PUBLIC_API_BASE` |
| dev.to / Medium | the two blog posts | free |
| Loom | 3-min demo video | free tier |

## Deferred (don't create yet)

- **Clerk or Firebase Auth** — real per-user login (v2; anonymous sessions
  work for friends-and-family)
- **Google OAuth consent screen** — only when GoogleCalendar adapter lands
- **Terra API** — wearable live-sync (backlog)

## Generated secrets (no account)

- `API_AUTH_TOKEN` — `openssl rand -hex 32`, stored in Secret Manager
- `DATABASE_URL` — when you create Cloud SQL Postgres (Phase 2+ durability)

Order to do them: GCP → OpenWeather + Places (Phase 0 runs end-to-end) →
E2B (Phase 2 analysis) → LangSmith + Ticketmaster (nicer Phase 3/4) →
GitHub secrets + Vercel (ship).
