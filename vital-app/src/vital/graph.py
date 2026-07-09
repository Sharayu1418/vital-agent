"""The VITAL graph — Phase 2: memory-aware agents + memory writer.

Topology (D11: security lives here, not in prompts):

    START → supervisor ⇄ {activity_scout | sleep_energy | idea_generator}
                              each agent → memory_writer → supervisor → END

Memory flow: agent nodes get relevant facts injected as a system message;
after each agent turn the writer extracts new stable facts (Flash — it's
an extraction task, D5).
"""
import atexit
from contextlib import AsyncExitStack, ExitStack

from langchain_core.messages import SystemMessage
from langchain_google_vertexai import ChatVertexAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command

from vital import memory
from vital.agents import (activity_scout, idea_generator, people_connector,
                          sleep_energy)
from vital.calendar import LocalCalendar
from vital.config import settings
from vital.planner import make_commit_plan, make_planner, make_request_approval
from vital.state import VitalState
from vital.supervisor import make_supervisor

_CHECKPOINTER_STACK = ExitStack()
atexit.register(_CHECKPOINTER_STACK.close)

_ASYNC_CHECKPOINTER_STACK = AsyncExitStack()

async def close_graph_resources():
    await _ASYNC_CHECKPOINTER_STACK.aclose()


def _agent_node(agent, store):
    """Wrap a compiled ReAct agent as a node, with memory injection."""
    def node(state: VitalState) -> Command:
        messages = list(state["messages"])
        last_user = next((m.content for m in reversed(messages)
                          if getattr(m, "type", "") == "human"), "")
        facts = memory.recall(store, state["user_id"], str(last_user))
        if facts:
            messages = [SystemMessage(content="Known about this user (use it, "
                                      "don't re-ask): " + "; ".join(facts))] + messages
        result = agent.invoke({"messages": messages})
        return Command(goto="memory_writer",
                       update={"messages": [result["messages"][-1]]})
    return node


def _memory_writer(store, llm):
    def node(state: VitalState) -> Command:
        # last human + AI exchange is enough context for fact extraction
        tail = state["messages"][-4:]
        transcript = "\n".join(f"{m.type}: {m.content}" for m in tail)
        try:
            memory.remember(store, state["user_id"], transcript, llm)
        except Exception:
            pass  # memory must never break the conversation; log in Phase 4
        return Command(goto="supervisor")
    return node


def build_graph(checkpointer=None, store=None):
    cfg = settings()
    store = store or memory.get_store()
    flash = ChatVertexAI(model=cfg.vital_model, temperature=0.0,
                         project=cfg.google_cloud_project,
                         location=cfg.google_cloud_location)

    builder = StateGraph(VitalState)
    builder.add_node("supervisor", make_supervisor(flash))
    builder.add_node("activity_scout", _agent_node(activity_scout.build_agent(), store))
    builder.add_node("sleep_energy", _agent_node(sleep_energy.build_agent(), store))
    builder.add_node("idea_generator", _agent_node(idea_generator.build_agent(), store))
    builder.add_node("people_connector", _agent_node(people_connector.build_agent(), store))
    builder.add_node("memory_writer", _memory_writer(store, flash))
    # Phase 3 HITL chain. Topology = security (D11): commit_plan is reachable
    # ONLY via request_approval's resume — no other edge leads to it.
    builder.add_node("planner", make_planner(flash))  # Pro only if evals demand it (D5)
    builder.add_node("request_approval", make_request_approval())
    builder.add_node("commit_plan", make_commit_plan(LocalCalendar()))
    builder.add_edge(START, "supervisor")

    if checkpointer is None:
        checkpointer = _default_checkpointer()
    return builder.compile(checkpointer=checkpointer, store=store)


async def build_graph_async(checkpointer=None, store=None):
    cfg = settings()
    store = store or memory.get_store()
    flash = ChatVertexAI(model=cfg.vital_model, temperature=0.0,
                         project=cfg.google_cloud_project,
                         location=cfg.google_cloud_location)

    builder = StateGraph(VitalState)
    builder.add_node("supervisor", make_supervisor(flash))
    builder.add_node("activity_scout", _agent_node(activity_scout.build_agent(), store))
    builder.add_node("sleep_energy", _agent_node(sleep_energy.build_agent(), store))
    builder.add_node("idea_generator", _agent_node(idea_generator.build_agent(), store))
    builder.add_node("people_connector", _agent_node(people_connector.build_agent(), store))
    builder.add_node("memory_writer", _memory_writer(store, flash))
    builder.add_node("planner", make_planner(flash))
    builder.add_node("request_approval", make_request_approval())
    builder.add_node("commit_plan", make_commit_plan(LocalCalendar()))
    builder.add_edge(START, "supervisor")

    if checkpointer is None:
        checkpointer = await _default_async_checkpointer()
    return builder.compile(checkpointer=checkpointer, store=store)


async def _default_async_checkpointer():
    cfg = settings()
    if cfg.database_url:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        saver = await _ASYNC_CHECKPOINTER_STACK.enter_async_context(
            AsyncPostgresSaver.from_conn_string(cfg.database_url)
        )
        await saver.setup()
        return saver
    return MemorySaver()


def _default_checkpointer():
    """MemorySaver locally (state lost on restart — fine for dev).
    Set DATABASE_URL for durable Postgres checkpoints (D3)."""
    cfg = settings()
    if cfg.database_url:
        from langgraph.checkpoint.postgres import PostgresSaver
        saver = _CHECKPOINTER_STACK.enter_context(
            PostgresSaver.from_conn_string(cfg.database_url)
        )
        saver.setup()  # idempotent table creation
        return saver
    return MemorySaver()
