"""The VITAL graph — Phase 2: memory-aware agents + memory writer.

Topology (D11: security lives here, not in prompts):

    START → supervisor → {activity_scout | sleep_energy | idea_generator
                          | people_connector} → END

One supervisor call, one specialist, done. Agents no longer return to the
supervisor: it could not tell that an agent had already answered, so it
re-routed on the same user message until the hop guard fired — 5x the
latency and tokens on every turn.

Memory flow: agent nodes get relevant facts injected as a system message;
extraction now happens in the API AFTER the response is sent (see
write_memories), not as a node between the answer and `done`.
"""
import atexit
from contextlib import AsyncExitStack, ExitStack
from functools import lru_cache

from langchain_core.messages import SystemMessage
from langchain_google_vertexai import ChatVertexAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from vital import memory, storage
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


def trim_history(messages, limit: int | None = None) -> list:
    """The most recent slice of a conversation (P1-6).

    Every turn used to resend the ENTIRE thread. Cost and latency grew
    linearly with thread length, and a long enough conversation would
    eventually exceed the context window and fail mid-chat with no recovery
    path — the user having no idea they were supposed to start a new one.

    Durable facts already survive in long-term memory, which is injected
    separately, so old turns are the cheapest thing to drop. Kept as a pure
    function so the boundary behaviour is testable without a graph.
    """
    limit = limit or settings().history_limit
    messages = list(messages)
    if len(messages) <= limit:
        return messages
    return messages[-limit:]


def _agent_node(agent, store):
    """Wrap a compiled ReAct agent as a node, with memory injection.

    Goes straight to END. It used to return to the supervisor via the memory
    writer, on the theory that the supervisor might chain a second
    specialist. In production that theory cost 5x: the supervisor could not
    tell an agent had already answered, so it re-routed until the hop guard
    fired. One specialist per message is also what reads well — chained
    agents concatenated into a single bubble with no separator.
    """
    def node(state: VitalState) -> Command:
        messages = trim_history(state["messages"])
        last_user = next((m.content for m in reversed(messages)
                          if getattr(m, "type", "") == "human"), "")
        facts = memory.recall(store, state["user_id"], str(last_user))
        context = []
        if facts:
            context.append("Known about this user (use it, don't re-ask): "
                           + "; ".join(facts))

        # Location LAST, and stated as authoritative, because it has to beat
        # the memory line above. Memory legitimately holds "User is in
        # Albany" from an earlier conversation; if they have since moved or
        # travelled, the browser knows and the stored fact does not. The
        # side panel used to show one city while the tools queried another,
        # with nothing in the system able to notice the disagreement.
        here = storage.current_location.get()
        if here:
            context.append(
                f"The user's CURRENT location is {here['label']} "
                f"(latitude {here['lat']}, longitude {here['lng']}). "
                "This is live from their device and OVERRIDES any location "
                "in the stored facts above. Use it for weather, venue and "
                "event searches without asking them where they are.")

        if context:
            messages = [SystemMessage(content="\n\n".join(context))] + messages
        result = agent.invoke({"messages": messages})
        return Command(goto=END, update={"messages": [result["messages"][-1]]})
    return node


@lru_cache
def _extractor_llm():
    cfg = settings()
    return ChatVertexAI(model=cfg.vital_model, temperature=0.0,
                        project=cfg.google_cloud_project,
                        location=cfg.google_cloud_location)


def write_memories(user_id: str, messages, store=None, llm=None) -> int:
    """Extract durable facts from the tail of a conversation.

    Deliberately NOT a graph node any more. As a node it sat between the
    answer and the `done` event, so the composer stayed locked while a model
    call ran that contributes nothing the user can see. The API now calls
    this AFTER emitting `done` but still inside the request, so the user is
    unblocked immediately and Cloud Run keeps the CPU allocated.

    Best-effort by design: memory must never break a conversation. If the
    client disconnects the moment it gets `done`, this may not run at all,
    and that is an acceptable trade for an unblocked composer.
    """
    tail = list(messages)[-4:]
    if not tail:
        return 0
    transcript = "\n".join(
        f"{getattr(m, 'type', 'msg')}: {getattr(m, 'content', '')}" for m in tail)
    try:
        return memory.remember(store or memory.get_store(), user_id,
                               transcript, llm or _extractor_llm())
    except Exception:
        return 0


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
