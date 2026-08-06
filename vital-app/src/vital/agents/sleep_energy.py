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
def analyze_sleep_data(question: str) -> dict:
    """Run a real Python/pandas analysis over the user's UPLOADED sleep data
    (Apple Health / CSV). Use for anything statistical: sleep debt over weeks,
    bedtime consistency, weekday-vs-weekend patterns, trends.
    Ask a specific question, e.g. 'sleep debt vs 8h target over the last
    14 days' or 'bedtime standard deviation weekdays vs weekends'.
    Returns {'insight': ...}, or {'no_data': ...} when nothing is uploaded,
    or {'error': ...} when live analysis is down — in which case say so and
    fall back to get_sleep_history. This may take ~10-20 seconds."""
    from vital import ingest
    from vital.analysis import run_analysis

    data = ingest.sleep_csv_bytes(storage.current_user_id.get())
    if data is None:
        # NOT an error: nothing is broken, the user simply hasn't uploaded.
        # Distinct key so it doesn't inflate the tool failure rate.
        return {"no_data": (
            "no uploaded sleep data — the user can upload an Apple Health "
            "export or CSV, or you can use get_sleep_history for manually "
            "logged nights")}
    try:
        return {"insight": run_analysis(question, data, ingest.csv_preview(data))}
    except Exception as exc:  # E2B key/quota/timeout, Vertex errors, ...
        # infra failure must degrade the answer, not kill the conversation
        # (D6 policy). Returns a dict with `error` like every other tool, so
        # the central tool-health logging in api.py can see E2B failing —
        # as a bare string this was the one paid dependency invisible to it.
        return {"error": f"analysis unavailable ({type(exc).__name__})"}


@tool
def forecast_energy(horizon_hours: int = 24) -> dict:
    """Predict the user's energy over the next 24-72 hours.

    Use this for ANY question about when to do something, when they'll be
    sharp or flat, or how today will go. Returns a curve plus the peak and
    dip windows in their local clock time, a confidence 0-1, and a `basis`
    sentence describing what the prediction rests on.

    ALWAYS quote the local times it gives you rather than restating a
    generic rule, and if confidence is below 0.4 say plainly that this is a
    typical pattern rather than theirs."""
    from vital import forecast as engine
    from vital import storage

    nights = engine.nights_from_rows(
        storage.sleep_history(engine.DEBT_WINDOW_NIGHTS * 2),
        storage.health_rows(storage.current_user_id.get()))
    result = engine.forecast(nights, storage.local_now(), horizon_hours)
    return summarize(result)


def summarize(result) -> dict:
    """Flatten a Forecast into something a model can quote accurately.

    Handing over 49 raw points invites the model to do arithmetic on them,
    which it is bad at. The peak and dip are computed here, in the user's
    clock, so the agent's job is quoting rather than deriving.
    """
    peak, trough = result.peak(), result.trough()
    return {
        "confidence": result.confidence,
        "basis": result.basis,
        "typical_wake": result.typical_wake.strftime("%H:%M"),
        "typical_bedtime": result.typical_bedtime.strftime("%H:%M"),
        "sleep_debt_hours": result.chronic_debt_h,
        "last_night_deficit_hours": result.acute_deficit_h,
        "peak": ({"at": peak.at.strftime("%a %H:%M"),
                  "energy": peak.energy, "why": peak.drivers[:2]}
                 if peak else None),
        "dip": ({"at": trough.at.strftime("%a %H:%M"),
                 "energy": trough.energy, "why": trough.drivers[:2]}
                if trough else None),
        "curve": [{"at": p.at.strftime("%a %H:%M"), "energy": p.energy}
                  for p in result.waking_points()][::2],
    }


SYSTEM_PROMPT = """You are VITAL's Sleep & Energy agent. Be concrete and \
actionable — never lecture about sleep hygiene generically.

When the user reports sleep or tiredness:
1. Log it if they gave you last night's numbers (log_sleep).
2. Pull history (get_sleep_history) before analyzing anything.
3. For statistical questions (trends, consistency, weekly patterns), use
   analyze_sleep_data — it runs real pandas code on their uploaded data.
   Prefer it over manual math whenever they have uploaded data. It returns
   {'insight': ...} to use, {'no_data': ...} meaning they haven't uploaded
   anything yet (invite them to, don't call it an error), or {'error': ...}
   meaning live analysis is down (say so, then fall back to
   get_sleep_history for a best-effort answer).
4. For anything forward-looking — when to schedule, when they'll be sharp
   or flat, how today will go — call forecast_energy. Quote the local
   times it returns. Do NOT restate the generic "peak 3-5h after wake"
   rule: the tool computes that from their own wake time and sleep debt,
   and its answer is the one to use. Report its `basis` when confidence is
   below 0.4, so a population curve is never passed off as theirs.
5. Report: sleep debt vs an 8h/night target over the window you have,
   tonight's target bedtime (specific time), and today's predicted peak
   and dip as clock times from forecast_energy, with what to schedule in
   each.

If they have energy to burn despite poor sleep, acknowledge it and suggest
low-intensity options — do not hand them off yourself; the supervisor decides.
Under 150 words. Numbers over platitudes."""


def build_agent():
    cfg = settings()
    llm = ChatVertexAI(model=cfg.vital_model, temperature=0.2,
                       project=cfg.google_cloud_project, location=cfg.google_cloud_location)
    return create_react_agent(
        llm,
        tools=[log_sleep, get_sleep_history, analyze_sleep_data, forecast_energy],
        prompt=SYSTEM_PROMPT)
