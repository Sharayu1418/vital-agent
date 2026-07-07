"""FastAPI entrypoint — Phase 1: full graph, threads, per-agent streaming.

Stateless (D3): thread_id keys all conversation state in the checkpointer.

Identity model (interim until real auth in Phase 5) — see security.py:
- Trusted callers (bearer token) may assert user_id.
- Anonymous callers get server-issued session cookies; their state lives
  under `anon-<session>:<thread>` — no collisions, nothing guessable.
- Debug routes exist only with DEBUG_ENDPOINTS=true, which refuses to
  boot without a token, and always require that token.
"""
from contextlib import asynccontextmanager

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from vital import ingest, memory
from vital.config import settings
from vital.graph import build_graph
from vital.security import (SESSION_COOKIE, caller_is_trusted,
                            resolve_identity, validate_startup)
from vital.storage import current_user_id

graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    validate_startup()  # fail closed before serving anything
    graph = build_graph()
    yield


app = FastAPI(title="VITAL", version="0.2.2", lifespan=lifespan)


def _set_session(response: Response, new_session: str | None) -> None:
    """Every route that resolves identity MUST call this — otherwise a new
    anonymous user's data lands under an ID their browser never receives."""
    if new_session:
        response.set_cookie(SESSION_COOKIE, new_session, httponly=True,
                            secure=settings().session_cookie_secure,
                            samesite="lax", max_age=30 * 24 * 3600)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str = Field(default="demo", max_length=64, pattern=r"^[\w-]+$")
    user_id: str = Field(default="local-user", max_length=64, pattern=r"^[\w-]+$")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(
    req: ChatRequest,
    trusted: bool = Depends(caller_is_trusted),
    vital_session: str | None = Cookie(default=None),
) -> EventSourceResponse:
    user_id, new_session = resolve_identity(req.user_id, trusted, vital_session)
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

    response = EventSourceResponse(stream())
    _set_session(response, new_session)
    return response


class Identity:
    """Dependency bundle: resolved user_id + session cookie to set (if new)."""
    def __init__(self, trusted: bool = Depends(caller_is_trusted),
                 vital_session: str | None = Cookie(default=None)):
        self.trusted = trusted
        self.session_cookie = vital_session

    def resolve(self, req_user_id: str = "local-user") -> tuple[str, str | None]:
        return resolve_identity(req_user_id, self.trusted, self.session_cookie)


@app.post("/upload/health")
async def upload_health(file: UploadFile, response: Response,
                        ident: Identity = Depends()) -> dict:
    """Apple Health export.xml or a sleep CSV → normalized per-user store.
    Anonymous users can upload too — their data lives under their session."""
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (50MB max)")
    try:
        if (file.filename or "").endswith(".xml"):
            rows = ingest.parse_apple_health_xml(content)
        else:
            rows = ingest.parse_sleep_csv(content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    ingest.save_sleep_data(user_id, rows)
    return {"nights_imported": len(rows),
            "date_range": [rows[0]["date"], rows[-1]["date"]]}


@app.get("/memories")
async def list_memories(response: Response, ident: Identity = Depends()) -> dict:
    """What VITAL knows about you — transparency + debugging (Phase 2B)."""
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"memories": memory.all_memories(memory.get_store(), user_id)}


@app.delete("/memories/{key}")
async def delete_memory(key: str, response: Response,
                        ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    memory.forget(memory.get_store(), user_id, key)
    return {"deleted": key}


if settings().debug_endpoints:  # route does not exist unless explicitly enabled

    @app.get("/debug/state/{user_id}/{thread_id}")
    async def debug_state(user_id: str, thread_id: str,
                          trusted: bool = Depends(caller_is_trusted)) -> dict:
        """Inspect a thread: routing path + transcript. Dev-only.
        validate_startup() guarantees a token exists; require it unconditionally."""
        if not trusted:
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
