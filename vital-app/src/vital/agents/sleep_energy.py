"""Sleep & Energy agent v1 (manual logs; sandboxed analysis arrives Phase 2)."""
from langchain_core.tools import tool
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

from vital import storage
from vital.config import settings


@tool
def log_sleep(bedtime: str, wake_time: str, quality: int) -> str:
    """Record last night's sleep. bedtime/wake_time as 'HH:MM' 24h format
    (e.g. '23:30', '07:00'), quality 1 (awful) to 5 (great).
    Duration is computed automatically — do NOT calculate it yourself.
    If this returns an 'invalid' message, ask the user to clarify the times."""
    try:
        duration = storage.log_sleep(bedtime, wake_time, quality)
        return f"logged: {duration} minutes"
    except ValueError as exc:
        return f"invalid: {exc}"


@tool
def get_sleep_history(days: int = 14) -> list[dict]:
    """Fetch the user's recent sleep logs (most recent first).
    Use before making any claim about their sleep patterns or debt."""
    return storage.sleep_history(days)


@tool
def analyze_sleep_data(question: str) -> str:
    """Run a real Python/pandas analysis over the user's UPLOADED sleep data
    (Apple Health / CSV). Use for anything statistical: sleep debt over weeks,
    bedtime consistency, weekday-vs-weekend patterns, trends.
    Ask a specific question, e.g. 'sleep debt vs 8h target over the last
    14 days' or 'bedtime standard deviation weekdays vs weekends'.
    Returns a plain-language insight, or a note if no data is uploaded.
    This may take ~10-20 seconds."""
    from vital import ingest
    from vital.analysis import run_analysis

    path = ingest.user_sleep_csv(storage.current_user_id.get())
    if path is None:
        return ("no uploaded sleep data — the user can upload an Apple Health "
                "export or CSV at /upload/health, or you can use "
                "get_sleep_history for manually logged nights")
    try:
        return run_analysis(question, path.read_bytes(), ingest.csv_preview(path))
    except Exception as exc:  # E2B key/quota/timeout, Vertex errors, ...
        # infra failure must degrade the answer, not kill the conversation (D6 policy)
        return ("analysis temporarily unavailable "
                f"({type(exc).__name__}) — tell the user live analysis is down, "
                "then fall back to get_sleep_history for a best-effort answer")


SYSTEM_PROMPT = """You are VITAL's Sleep & Energy agent. Be concrete and \
actionable — never lecture about sleep hygiene generically.

When the user reports sleep or tiredness:
1. Log it if they gave you last night's numbers (log_sleep).
2. Pull history (get_sleep_history) before analyzing anything.
3. For statistical questions (trends, consistency, weekly patterns), use
   analyze_sleep_data — it runs real pandas code on their uploaded data.
   Prefer it over manual math whenever they have uploaded data.
4. Report: sleep debt vs an 8h/night target over the window you have,
   tonight's target bedtime (specific time), and today's likely energy
   peak (~3-5h after wake) and dip (~7-9h after wake) with what to
   schedule in each.

If they have energy to burn despite poor sleep, acknowledge it and suggest
low-intensity options — do not hand them off yourself; the supervisor decides.
Under 150 words. Numbers over platitudes."""


def build_agent():
    cfg = settings()
    llm = ChatVertexAI(model=cfg.vital_model, temperature=0.2,
                       project=cfg.google_cloud_project, location=cfg.google_cloud_location)
    return create_react_agent(llm, tools=[log_sleep, get_sleep_history, analyze_sleep_data],
                              prompt=SYSTEM_PROMPT)
