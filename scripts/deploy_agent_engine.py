"""Deploy the VITAL graph to Vertex AI Agent Engine (Phase 4, deploy path A).

Run from repo root after Cloud Run is already working (path B):

    uv run python scripts/deploy_agent_engine.py

What Agent Engine gives you vs your Cloud Run deploy: managed sessions,
built-in tracing, autoscaling, IAM-native auth. What you give up: your
FastAPI middleware (auth/guardrails/SSE shaping run OUTSIDE the engine —
you keep the FastAPI layer and point it at the engine, or accept engine-
native auth). Run both for a week; write up the comparison.

Prereqs:
  gcloud services enable aiplatform.googleapis.com
  A GCS staging bucket: gs://<project>-agent-engine-staging
"""
import vertexai
from vertexai import agent_engines

from vital.config import settings


def graph_factory():
    """Agent Engine calls this in ITS runtime — imports stay inside."""
    from vital.graph import build_graph
    return build_graph()   # engine provides managed checkpointing/sessions


def main() -> None:
    cfg = settings()
    vertexai.init(project=cfg.google_cloud_project,
                  location=cfg.google_cloud_location,
                  staging_bucket=f"gs://{cfg.google_cloud_project}-agent-engine-staging")

    app = agent_engines.create(
        agent_engine=graph_factory,
        display_name="vital",
        requirements=[
            "langgraph>=0.4",
            "langchain-google-vertexai>=2.0",
            "langgraph-checkpoint-postgres>=2.0",
            "pydantic-settings>=2.6",
            "httpx>=0.28",
            "e2b-code-interpreter>=1.0",  # sleep agent's analyze tool imports it at runtime
        ],
        # env the graph needs inside the engine runtime:
        env_vars=["OPENWEATHER_API_KEY", "GOOGLE_PLACES_API_KEY",
                  "TICKETMASTER_API_KEY", "E2B_API_KEY", "DATABASE_URL"],
    )
    print(f"deployed: {app.resource_name}")
    print("test it:  app.stream_query(input=..., session_id=...)")


if __name__ == "__main__":
    main()
