"""The generate → gate → execute → repair → interpret subgraph (Phase 2).

This is the canonical self-correcting code-gen loop:

    START → write_code → execute ──ok──▶ interpret → END
                            ▲  │error
                            │  ▼
                          repair   (attempts < max, else → failed → END)

llm and runner are injected, so the whole control flow is testable with
fakes — no E2B, no Vertex.
"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from vital import storage
from vital.sandbox import RunnerFn, check_code_safety

CODE_PROMPT = """Write Python (pandas) code to answer this question about \
the user's sleep data: {task}

The data is at /data/sleep.csv. Preview:
{preview}

Rules:
- Only pandas, numpy, math, statistics, datetime, json.
- Read with pd.read_csv('/data/sleep.csv').
- print() every result with a clear label.
- No plotting, no file writes, no network, no os/subprocess.
Return ONLY the code. No markdown fences, no explanation."""

REPAIR_PROMPT = """This pandas code failed:
```
{code}
```
Error:
{error}

Data preview (the columns that actually exist):
{preview}

Fix it. Return ONLY the corrected code, no fences, no explanation."""

INTERPRET_PROMPT = """A pandas analysis answering '{task}' printed:

{stdout}

Turn this into 2-4 sentences of concrete, actionable insight for the user.
Use the actual numbers. No hedging, no code talk."""


class AnalysisState(TypedDict, total=False):
    task: str
    preview: str
    code: str
    attempts: int
    stdout: str
    error: str | None
    insight: str


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop ```python
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def build_analysis_graph(llm, runner: RunnerFn, max_attempts: int = 3):

    def write_code(state: AnalysisState) -> Command:
        code = _strip_fences(llm.invoke(
            CODE_PROMPT.format(task=state["task"], preview=state["preview"])
        ).content)
        return Command(goto="execute", update={"code": code, "attempts": 0})

    def execute(state: AnalysisState) -> Command:
        ok, reason = check_code_safety(state["code"])
        if not ok:
            # safety rejection is fed to repair like any error — the model
            # usually just used a banned convenience import
            result = {"stdout": "", "error": f"code rejected by safety gate: {reason}"}
        else:
            result = runner(state["code"])
        storage.log_sandbox_run(state["code"], ok=result["error"] is None,
                                error=result["error"])
        if result["error"] is None:
            return Command(goto="interpret", update={"stdout": result["stdout"], "error": None})
        if state.get("attempts", 0) + 1 >= max_attempts:
            return Command(goto="failed", update={"error": result["error"]})
        return Command(goto="repair", update={"error": result["error"]})

    def repair(state: AnalysisState) -> Command:
        code = _strip_fences(llm.invoke(
            REPAIR_PROMPT.format(code=state["code"], error=state["error"],
                                 preview=state["preview"])
        ).content)
        return Command(goto="execute",
                       update={"code": code, "attempts": state.get("attempts", 0) + 1})

    def interpret(state: AnalysisState) -> Command:
        insight = llm.invoke(
            INTERPRET_PROMPT.format(task=state["task"], stdout=state["stdout"])
        ).content
        return Command(goto=END, update={"insight": insight})

    def failed(state: AnalysisState) -> Command:
        return Command(goto=END, update={
            "insight": ("I couldn't complete this analysis after several tries "
                        f"(last error: {str(state.get('error'))[:200]}). "
                        "The data may be in an unexpected format.")})

    builder = StateGraph(AnalysisState)
    builder.add_node("write_code", write_code)
    builder.add_node("execute", execute)
    builder.add_node("repair", repair)
    builder.add_node("interpret", interpret)
    builder.add_node("failed", failed)
    builder.add_edge(START, "write_code")
    return builder.compile()


def run_analysis(task: str, csv_bytes: bytes, preview: str) -> str:
    """Production entrypoint: real Vertex LLM + real E2B runner."""
    from langchain_google_vertexai import ChatVertexAI

    from vital.config import settings
    from vital.sandbox import make_e2b_runner

    cfg = settings()
    llm = ChatVertexAI(model=cfg.vital_model, temperature=0.1,
                       project=cfg.google_cloud_project, location=cfg.google_cloud_location)
    graph = build_analysis_graph(llm, make_e2b_runner({"sleep.csv": csv_bytes}),
                                 max_attempts=cfg.max_repair_attempts)
    result = graph.invoke({"task": task, "preview": preview})
    return result.get("insight", "analysis produced no output")
