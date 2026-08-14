# Phase 0 — Foundations (Week 1)

**Goal:** One working agent (Activity Scout), deployed to Cloud Run, callable from a public URL by end of week.

---

## Objectives

- GCP project + Vertex AI auth working locally
- LangGraph ReAct agent with 2 real tools (weather, places)
- FastAPI wrapper with streaming
- Dockerized, deployed to Cloud Run

## Prerequisites

- Python 3.11+, Docker Desktop, gcloud CLI
- GCP account (new accounts get $300 free credits)
- API keys: OpenWeather (free tier), Google Places

## Step-by-step

### 1. GCP setup (Day 1)

```bash
gcloud projects create vital-agent-dev
gcloud config set project vital-agent-dev
gcloud services enable aiplatform.googleapis.com run.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com
gcloud auth application-default login
```

### 2. Repo scaffold (Day 1)

```
vital/
├── src/vital/
│   ├── agents/activity_scout.py
│   ├── tools/weather.py
│   ├── tools/places.py
│   ├── graph.py
│   └── api.py            # FastAPI app
├── tests/
├── Dockerfile
├── pyproject.toml        # use uv or poetry
└── .env.example
```

```bash
uv init && uv add langgraph langchain-google-vertexai fastapi uvicorn \
  httpx pydantic-settings sse-starlette
```

### 3. Tools (Day 2)

Each tool = typed function with docstring (the docstring IS the tool description the LLM sees — write it carefully):

```python
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> dict:
    """Current weather + 12h forecast for a city.
    Use before recommending outdoor activities."""
    # httpx call to OpenWeather; return temp, condition, precip_prob
```

```python
@tool
def search_places(query: str, city: str, max_results: int = 5) -> list[dict]:
    """Search real venues/activities (e.g. 'bouldering gym', 'hiking trail').
    Returns name, rating, address, maps_url."""
    # Google Places Text Search API
```

### 4. Agent (Day 3)

```python
from langgraph.prebuilt import create_react_agent
from langchain_google_vertexai import ChatVertexAI

llm = ChatVertexAI(model="gemini-2.5-flash", temperature=0.3)

agent = create_react_agent(
    llm,
    tools=[get_weather, search_places],
    prompt="""You are Activity Scout. Given a user's energy level, mood,
    location and free time, recommend 3 concrete activities (mix
    indoor/outdoor based on weather). Always check weather first for
    outdoor options. Return real venues with links, never generic advice.""",
)
```

Test in a notebook/REPL: "I'm in Brooklyn, high energy, 3 free hours Saturday afternoon."

### 5. FastAPI + streaming (Day 4)

```python
@app.post("/chat")
async def chat(req: ChatRequest):
    async def stream():
        async for event in agent.astream_events(
            {"messages": [("user", req.message)]}, version="v2"
        ):
            if event["event"] == "on_chat_model_stream":
                yield event["data"]["chunk"].content
    return EventSourceResponse(stream())
```

### 6. Deploy (Day 5)

```dockerfile
FROM python:3.11-slim
COPY . /app
WORKDIR /app
RUN pip install uv && uv sync --frozen
CMD ["uv", "run", "uvicorn", "src.vital.api:app", "--host", "0.0.0.0", "--port", "8080"]
```

```bash
gcloud run deploy vital --source . --region us-east1 \
  --allow-unauthenticated --set-secrets=OPENWEATHER_KEY=openweather:latest
```

Cloud Run's service account needs `roles/aiplatform.user` for Vertex calls.

## Concepts you learn

ReAct loop mechanics, tool schemas from type hints/docstrings, Vertex AI auth (ADC vs service accounts), SSE streaming, Cloud Run deploys.

## Pitfalls

- **Vertex auth on Cloud Run:** don't ship a JSON key; use the service's identity (ADC picks it up automatically)
- **Tool descriptions too vague** → agent never calls them or calls with garbage args. Iterate on docstrings like prompts.
- **Flash vs Pro:** Flash is fine here and ~10x cheaper. Don't touch Pro until Phase 1 planning tasks.

## Definition of done

- [ ] `curl` your Cloud Run URL, get a streamed activity recommendation with real venue links
- [ ] Weather tool demonstrably changes indoor/outdoor mix
- [ ] README with setup instructions
- [ ] Total spend so far < $5
