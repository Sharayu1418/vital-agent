"""FastAPI entrypoint — Phase 1: full graph, threads, per-agent streaming.

Stateless (D3): thread_id keys all conversation state in the checkpointer.

Identity model (interim until real auth in Phase 5):
- Callers presenting the shared bearer token (API_AUTH_TOKEN) are trusted
  infrastructure and may assert a user_id.
- Anonymous callers are pinned to 'local-user' — they can never read or
  write another user's threads, no matter what they send.
- /debug/* routes are compiled in only when DEBUG_ENDPOINTS=true.
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from vital.config import settings
from vital.graph import build_graph
from vital.security import caller_is_trusted, resolve_user_id
from vital.storage import current_user_id

graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    graph = build_graph()
    yield


app = FastAPI(title="VITAL", version="0.2.1", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str = Field(default="demo", max_length=64, pattern=r"^[\w-]+$")
    user_id: str = Field(default="local-user", max_length=64, pattern=r"^[\w-]+$")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest, trusted: bool = Depends(caller_is_trusted)) -> EventSourceResponse:
    user_id = resolve_user_id(req.user_id, trusted)
    current_user_id.set(user_id)  # tools read identity from here, never from the LLM
    config = {"configurable": {"thread_id": f"{user_id}:{req.thread_id}"}}

    async def stream():
        async for event in graph.astream_events(
            {"messages": [("user", req.message)], "user_id": user_id,
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


if settings().debug_endpoints:  # route does not exist unless explicitly enabled

    @app.get("/debug/state/{user_id}/{thread_id}")
    async def debug_state(user_id: str, thread_id: str,
                          trusted: bool = Depends(caller_is_trusted)) -> dict:
        """Inspect a thread: routing path + transcript. Dev-only."""
        if settings().api_auth_token and not trusted:
            raise HTTPException(status_code=401, detail="token required")
        snap = graph.get_state({"configurable": {"thread_id": f"{user_id}:{thread_id}"}})
        return {
            "routing_history": snap.values.get("routing_history", []),
            "message_count": len(snap.values.get("messages", [])),
            "messages": [
                {"type": m.type, "content": m.content[:200]}
                for m in snap.values.get("messages", [])
            ],
        }
