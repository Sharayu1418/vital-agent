"""FastAPI entrypoint — Phase 1: full graph, threads, per-agent streaming.

Still stateless (D3): thread_id keys all conversation state in the
checkpointer; any instance can serve any request.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from vital.graph import build_graph
from vital.storage import current_user_id

graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    graph = build_graph()
    yield


app = FastAPI(title="VITAL", version="0.2.0", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str = "demo"      # conversation continuity
    user_id: str = "local-user"  # real auth replaces this in Phase 5


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    current_user_id.set(req.user_id)  # tools read identity from here, never from the LLM
    config = {"configurable": {"thread_id": f"{req.user_id}:{req.thread_id}"}}

    async def stream():
        async for event in graph.astream_events(
            {"messages": [("user", req.message)], "user_id": req.user_id,
             "routing_history": []},  # reset loop guard each turn
            config=config, version="v2",
        ):
            kind = event["event"]
            node = event.get("metadata", {}).get("langgraph_node", "")
            if kind == "on_chat_model_stream" and node != "supervisor":
                chunk = event["data"]["chunk"].content
                if chunk:
                    yield {"event": "token", "data": chunk}
            elif kind == "on_tool_start":
                yield {"event": "status", "data": f"{node}: using {event['name']}"}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(stream())


@app.get("/debug/state/{user_id}/{thread_id}")
async def debug_state(user_id: str, thread_id: str) -> dict:
    """Inspect a thread: routing path + transcript. Your main checking tool."""
    snap = graph.get_state({"configurable": {"thread_id": f"{user_id}:{thread_id}"}})
    return {
        "routing_history": snap.values.get("routing_history", []),
        "message_count": len(snap.values.get("messages", [])),
        "messages": [
            {"type": m.type, "content": m.content[:200]}
            for m in snap.values.get("messages", [])
        ],
    }
