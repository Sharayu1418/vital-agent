# VITAL — Phases 0–1: Supervisor + 3 Agents

LangGraph supervisor routing between Activity Scout, Sleep & Energy, and Idea Generator agents on Vertex AI Gemini; streamed via FastAPI; deployed on Cloud Run. Architecture rationale: see `../01-design-decisions.md`.

## Layout

```
src/vital/
├── graph.py                    # supervisor topology + checkpointer (START HERE)
├── supervisor.py               # routing via structured output + few-shots
├── state.py                    # shared VitalState schema (D1)
├── agents/
│   ├── activity_scout.py       # weather + places
│   ├── sleep_energy.py         # sleep logs, debt, energy forecast
│   └── idea_generator.py       # interests → concrete ideas
├── tools/weather.py, places.py # thin adapters (D6)
├── storage.py                  # SQLite now, Postgres in Phase 2 (D2)
├── config.py                   # all knobs, 12-factor
└── api.py                      # stateless FastAPI + SSE + /debug/state (D3)
```

## Local dev

```bash
# 1. deps
uv sync --extra dev

# 2. GCP auth + project
gcloud auth application-default login
gcloud config set project vital-agent-dev
gcloud services enable aiplatform.googleapis.com

# 3. secrets
cp .env.example .env   # fill in OpenWeather + Places keys

# 4. tests (no keys needed — HTTP is mocked)
uv run pytest

# 5. run
uv run uvicorn vital.api:app --app-dir src --reload
```

Try it:

```bash
curl -N localhost:8000/chat -H 'content-type: application/json' \
  -d '{"message": "Im in Brooklyn, tons of energy, 3 free hours Saturday, on a budget"}'
```

## Deploy

```bash
# secrets to Secret Manager (never in the image)
printf '%s' "$OPENWEATHER_API_KEY" | gcloud secrets create openweather --data-file=-
printf '%s' "$GOOGLE_PLACES_API_KEY" | gcloud secrets create places --data-file=-

gcloud run deploy vital --source . --region us-east1 --allow-unauthenticated \
  --set-env-vars GOOGLE_CLOUD_PROJECT=vital-agent-dev \
  --set-secrets OPENWEATHER_API_KEY=openweather:latest,GOOGLE_PLACES_API_KEY=places:latest

# Vertex access for the service identity (no JSON keys, ever)
gcloud projects add-iam-policy-binding vital-agent-dev \
  --member serviceAccount:$(gcloud run services describe vital --region us-east1 \
    --format 'value(spec.template.spec.serviceAccountName)') \
  --role roles/aiplatform.user
```

## Phase 0 definition of done

- [ ] Streamed recommendation with real venue links from the Cloud Run URL
- [ ] Rain in forecast demonstrably shifts recommendations indoor
- [ ] `uv run pytest` green
- [ ] Spend < $5
