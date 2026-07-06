"""The VITAL graph: supervisor + sub-agents as subgraphs.

Topology (D11: security lives here, not in prompts):

    START → supervisor ⇄ {activity_scout | sleep_energy | idea_generator} → END
"""
from langchain_google_vertexai import ChatVertexAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command

from vital.agents import activity_scout, idea_generator, sleep_energy
from vital.config import settings
from vital.state import VitalState
from vital.supervisor import make_supervisor


def _agent_node(agent):
    """Wrap a compiled ReAct agent as a node. Only NEW messages flow back to
    shared state (keeps sub-agent tool chatter out of the parent transcript
    would be Phase-2 work; for now we return the final reply only)."""
    def node(state: VitalState) -> Command:
        result = agent.invoke({"messages": state["messages"]})
        return Command(goto="supervisor", update={"messages": [result["messages"][-1]]})
    return node


def build_graph(checkpointer=None):
    cfg = settings()
    router_llm = ChatVertexAI(model=cfg.vital_model, temperature=0.0,  # routing = deterministic
                              project=cfg.google_cloud_project,
                              location=cfg.google_cloud_location)

    builder = StateGraph(VitalState)
    builder.add_node("supervisor", make_supervisor(router_llm))
    builder.add_node("activity_scout", _agent_node(activity_scout.build_agent()))
    builder.add_node("sleep_energy", _agent_node(sleep_energy.build_agent()))
    builder.add_node("idea_generator", _agent_node(idea_generator.build_agent()))
    builder.add_edge(START, "supervisor")
    # sub-agents return via Command(goto="supervisor"); supervisor ends via Command(goto=END)

    if checkpointer is None:
        checkpointer = _default_checkpointer()
    return builder.compile(checkpointer=checkpointer)


def _default_checkpointer():
    """MemorySaver locally (state lost on restart — fine for dev).
    Set DATABASE_URL to get durable Postgres checkpoints (D3):

        from langgraph.checkpoint.postgres import PostgresSaver
        saver = PostgresSaver.from_conn_string(url); saver.setup()
    """
    cfg = settings()
    if cfg.database_url:
        from langgraph.checkpoint.postgres import PostgresSaver
        saver = PostgresSaver.from_conn_string(cfg.database_url)
        saver.setup()  # idempotent table creation
        return saver
    return MemorySaver()
