"""Sleep & Energy agent v1 (manual logs; sandboxed analysis arrives Phase 2)."""
from langchain_core.tools import tool
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

from vital import storage
from vital.config import settings


@tool
def log_sleep(bedtime: str, wake_time: str, quality: int, duration_min: int) -> str:
    """Record last night's sleep. bedtime/wake_time as 'HH:MM' (24h),
    quality 1 (awful) to 5 (great), duration_min = total minutes slept.
    Compute duration_min yourself from the times the user gives you."""
    storage.log_sleep(bedtime, wake_time, quality, duration_min)
    return "logged"


@tool
def get_sleep_history(days: int = 14) -> list[dict]:
    """Fetch the user's recent sleep logs (most recent first).
    Use before making any claim about their sleep patterns or debt."""
    return storage.sleep_history(days)


SYSTEM_PROMPT = """You are VITAL's Sleep & Energy agent. Be concrete and \
actionable — never lecture about sleep hygiene generically.

When the user reports sleep or tiredness:
1. Log it if they gave you last night's numbers (log_sleep).
2. Pull history (get_sleep_history) before analyzing anything.
3. Report: sleep debt vs an 8h/night target over the window you have,
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
    return create_react_agent(llm, tools=[log_sleep, get_sleep_history], prompt=SYSTEM_PROMPT)
