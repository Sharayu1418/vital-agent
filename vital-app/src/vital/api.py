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

from fastapi import (Cookie, Depends, FastAPI, Header, HTTPException, Response,
                     UploadFile)
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from vital import buddies, guardrails, ingest, memory, metrics, storage
from vital.config import settings
from vital.graph import build_graph_async, close_graph_resources
from vital.security import (SESSION_COOKIE, caller_is_trusted,
                            resolve_identity, validate_startup)
from vital.storage import current_user_id

graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    validate_startup()  # fail closed before serving anything
    graph = await build_graph_async()
    try:
        yield
    finally:
        await close_graph_resources()


app = FastAPI(title="VITAL", version="0.5.0", lifespan=lifespan)

# Phase 5: the Next.js frontend is a separate origin. Cookies carry identity,
# so allow_credentials=True and a SINGLE explicit origin (never "*" with
# credentials — browsers reject it, and it would be wrong anyway).
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Vital-Session"],
    expose_headers=["X-Vital-Session"],
)


@app.middleware("http")
async def csrf_origin_guard(request, call_next):
    """CSRF defense that activates exactly when it's needed: SameSite=None
    means foreign sites can send our cookie on POSTs. Browsers always attach
    an Origin header to cross-site fetches — reject mismatches. Requests
    without Origin (curl, server-to-server) pass; they carry no cookie jar."""
    cfg = settings()
    if cfg.session_cookie_samesite == "none" and request.method in {"POST", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin is not None and origin != cfg.frontend_origin:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403,
                                content={"detail": "cross-site request blocked"})
    return await call_next(request)


def _set_session(response: Response, new_session: str | None) -> None:
    """Every route that resolves identity MUST call this — otherwise a new
    anonymous user's data lands under an ID their browser never receives.

    Dual transport: httponly cookie for browsers, X-Vital-Session response
    header for the mobile app (RN networking doesn't do httponly cookies;
    the app stores the value and sends it back as a request header)."""
    if new_session:
        cfg = settings()
        response.set_cookie(SESSION_COOKIE, new_session, httponly=True,
                            secure=cfg.session_cookie_secure,
                            samesite=cfg.session_cookie_samesite,
                            max_age=30 * 24 * 3600)
        response.headers["X-Vital-Session"] = new_session


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str = Field(default="demo", max_length=64, pattern=r"^[\w-]+$")
    user_id: str = Field(default="local-user", max_length=64, pattern=r"^[\w-]+$")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


# nodes whose model output is machinery (routing decisions, plan JSON,
# fact extraction) — never stream their raw tokens to the user
_NON_USER_FACING = {"supervisor", "planner", "memory_writer"}


def visible_text(content) -> str:
    """Extract ONLY user-visible text from LangChain/Gemini message content.

    Vertex/Gemini chunk content is sometimes a list of content blocks
    (dicts with 'text' plus provider internals like 'thought_signature').
    Streaming the raw object leaks provider metadata to the UI — so this
    is a security/privacy boundary, not just formatting."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(content, list):
        return "".join(visible_text(part) for part in content)
    text = getattr(content, "text", None)
    return text if isinstance(text, str) else ""


async def _aget_graph_state(config):
    """Read graph state from async endpoints. Async-checkpointer graphs
    (prod: AsyncPostgresSaver) require aget_state; sync/in-memory graphs
    (local dev, tests) only have get_state. Prefer async, fall back to sync."""
    if hasattr(graph, "aget_state"):
        return await graph.aget_state(config)
    return graph.get_state(config)


def _graph_stream(graph_input, config, user_id: str):
    """Shared SSE generator for /chat and /approve. Returns the async
    generator OBJECT (callers hand it straight to EventSourceResponse).

    Besides tokens/status, emits:
    - approval_required: graph paused at request_approval (plan payload)
    - message: a final AI message that was written to state by a non-LLM
      node (commit/reject confirmations) and therefore never streamed
    """
    import json as _json

    async def stream():
        import time
        t0 = time.monotonic()
        streamed_tokens = False
        streamed_chars = 0
        run_config = {**config, "recursion_limit": settings().recursion_limit}
        async for event in graph.astream_events(graph_input, config=run_config, version="v2"):
            kind = event["event"]
            node = event.get("metadata", {}).get("langgraph_node", "")
            if kind == "on_chat_model_stream" and node not in _NON_USER_FACING:
                chunk = visible_text(event["data"]["chunk"].content)
                if chunk:
                    streamed_tokens = True
                    streamed_chars += len(chunk)
                    yield {"event": "token", "data": chunk}
            elif kind == "on_tool_start":
                yield {"event": "status", "data": f"{node}: using {event['name']}"}

        snap = await _aget_graph_state(config)
        pending = [intr for task in getattr(snap, "tasks", ())
                   for intr in getattr(task, "interrupts", ())]
        for intr in pending:  # paused at request_approval?
            yield {"event": "approval_required", "data": _json.dumps(intr.value)}

        if not streamed_tokens and not pending:
            # commit_plan / reject write their confirmation straight into
            # state — surface it, or the frontend shows nothing after approve
            messages = (getattr(snap, "values", None) or {}).get("messages", [])
            last = messages[-1] if messages else None
            if last is not None and getattr(last, "type", "") == "ai":
                msg = visible_text(getattr(last, "content", None))
                if msg:
                    yield {"event": "message", "data": msg}

        # Phase 4: usage + metrics. user_id comes from the CALLER's resolved
        # identity, never from graph state — state can lag or be absent on a
        # paused thread, and billing the wrong identity breaks the budget.
        values = getattr(snap, "values", None) or {}
        est = guardrails.estimate_tokens(str(graph_input)[:2000], "x" * streamed_chars)
        try:
            guardrails.record_usage(user_id, est)
        except Exception:
            pass  # accounting must never break the stream
        metrics.log_turn(user_id, str(config["configurable"]["thread_id"]),
                         routing_hops=len(values.get("routing_history", []) or []),
                         est_tokens=est,
                         duration_ms=int((time.monotonic() - t0) * 1000))

        yield {"event": "done", "data": ""}
    return stream()  # the generator object, not the function (review fix)


@app.post("/chat")
async def chat(
    req: ChatRequest,
    trusted: bool = Depends(caller_is_trusted),
    vital_session: str | None = Cookie(default=None),
    x_vital_session: str | None = Header(default=None),
) -> EventSourceResponse:
    user_id, new_session = resolve_identity(req.user_id, trusted,
                                            vital_session or x_vital_session)
    current_user_id.set(user_id)  # tools read identity from here, never from the LLM

    # Guardrail 1: crisis messages bypass the agent pipeline entirely —
    # deterministic path, no routing, no tools, no LLM dependency.
    if guardrails.crisis_check(req.message):
        async def crisis_stream():
            yield {"event": "message", "data": guardrails.CRISIS_RESPONSE}
            yield {"event": "done", "data": ""}
        metrics.log_turn(user_id, req.thread_id, 0, 0, 0, kind="crisis_response")
        response = EventSourceResponse(crisis_stream())
        _set_session(response, new_session)
        return response

    # Guardrail 2: per-user daily token budget
    if guardrails.budget_exceeded(user_id):
        raise HTTPException(status_code=429, detail=guardrails.BUDGET_MESSAGE)

    config = {"configurable": {"thread_id": f"{user_id}:{req.thread_id}"}}
    graph_input = {"messages": [("user", req.message)], "user_id": user_id,
                   "routing_history": []}  # reset loop guard each turn
    response = EventSourceResponse(_graph_stream(graph_input, config, user_id))
    _set_session(response, new_session)
    return response


class ApprovalRequest(BaseModel):
    thread_id: str = Field(default="demo", max_length=64, pattern=r"^[\w-]+$")
    user_id: str = Field(default="local-user", max_length=64, pattern=r"^[\w-]+$")
    action: str = Field(pattern=r"^(approve|edit|reject)$")
    feedback: str = Field(default="", max_length=1000)


@app.post("/approve")
async def approve(
    req: ApprovalRequest,
    trusted: bool = Depends(caller_is_trusted),
    vital_session: str | None = Cookie(default=None),
    x_vital_session: str | None = Header(default=None),
) -> EventSourceResponse:
    """Resume a paused plan-approval interrupt. The resume value reaches
    request_approval() exactly where interrupt() returned."""
    from langgraph.types import Command as ResumeCommand

    user_id, new_session = resolve_identity(req.user_id, trusted,
                                            vital_session or x_vital_session)
    current_user_id.set(user_id)
    # budget applies here too: an 'edit' resume re-invokes the planner LLM,
    # so /approve must not be a budget bypass (Phase 4 review finding)
    if guardrails.budget_exceeded(user_id):
        raise HTTPException(status_code=429, detail=guardrails.BUDGET_MESSAGE)
    config = {"configurable": {"thread_id": f"{user_id}:{req.thread_id}"}}
    if not any(t.interrupts for t in getattr(await _aget_graph_state(config), "tasks", ())):
        raise HTTPException(status_code=409, detail="nothing awaiting approval on this thread")
    resume = ResumeCommand(resume={"action": req.action, "feedback": req.feedback})
    response = EventSourceResponse(_graph_stream(resume, config, user_id))
    _set_session(response, new_session)
    return response


class Identity:
    """Dependency bundle: resolved user_id + session (cookie OR mobile header)."""
    def __init__(self, trusted: bool = Depends(caller_is_trusted),
                 vital_session: str | None = Cookie(default=None),
                 x_vital_session: str | None = Header(default=None)):
        self.trusted = trusted
        self.session = vital_session or x_vital_session

    def resolve(self, req_user_id: str = "local-user") -> tuple[str, str | None]:
        return resolve_identity(req_user_id, self.trusted, self.session)


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


# ---------- Side-panel data endpoints (Phase 5 UI) ----------

@app.get("/sleep/recent")
async def sleep_recent(response: Response, ident: Identity = Depends()) -> dict:
    """Last 14 nights, merging manual logs with uploaded data (upload wins
    on date conflicts) — feeds the side-panel trend chart."""
    import csv as _csv

    user_id, new_session = ident.resolve()
    current_user_id.set(user_id)  # sleep_history reads the contextvar —
    # without this it returns whoever's identity was set last (P1 bug)
    _set_session(response, new_session)
    nights: dict[str, dict] = {}
    for row in storage.sleep_history(30):
        nights[row["log_date"]] = {"date": row["log_date"],
                                   "duration_min": row["duration_min"],
                                   "quality": row["quality"], "source": "manual"}
    path = ingest.user_sleep_csv(user_id)
    if path:
        with path.open() as f:
            for row in _csv.DictReader(f):
                nights[row["date"]] = {"date": row["date"],
                                       "duration_min": int(row["duration_min"]),
                                       "quality": row.get("quality") or None,
                                       "source": row.get("source", "upload")}
    ordered = sorted(nights.values(), key=lambda n: n["date"])[-14:]
    return {"nights": ordered, "target_min": 480}


@app.get("/calendar")
async def calendar_view(response: Response, ident: Identity = Depends()) -> dict:
    """Committed plan events — the side panel's 'Your plan' section."""
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"events": storage.calendar_events(user_id)}


@app.get("/threads/{thread_id}/messages")
async def thread_messages(thread_id: str, response: Response,
                          ident: Identity = Depends()) -> dict:
    """Conversation history for thread switching. Identity-scoped by
    construction: the checkpointer key is '{user_id}:{thread_id}', so a
    caller can only ever read their own threads."""
    if not thread_id.replace("-", "").replace("_", "").isalnum() or len(thread_id) > 64:
        raise HTTPException(status_code=422, detail="invalid thread id")
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    snap = await _aget_graph_state({"configurable": {"thread_id": f"{user_id}:{thread_id}"}})
    values = getattr(snap, "values", None) or {}
    out = []
    for m in values.get("messages", []):
        role = getattr(m, "type", "")
        if role not in ("human", "ai"):
            continue  # tool chatter never reaches the UI
        text = visible_text(getattr(m, "content", None))
        if text:
            out.append({"role": role, "text": text})
    pending = [intr.value for task in getattr(snap, "tasks", ())
               for intr in getattr(task, "interrupts", ())]
    return {"messages": out, "pending_approval": pending[0] if pending else None}


class FeedbackRequest(BaseModel):
    thread_id: str = Field(default="demo", max_length=64, pattern=r"^[\w-]+$")
    rating: str = Field(pattern=r"^(up|down)$")
    comment: str = Field(default="", max_length=2000)


@app.post("/feedback")
async def feedback(req: FeedbackRequest, response: Response,
                   ident: Identity = Depends()) -> dict:
    """Thumbs per response — the Phase 5 iteration loop. Also mirrored to
    metrics so rating trends show up next to latency/cost."""
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    storage.save_feedback(user_id, req.thread_id, req.rating, req.comment)
    metrics.log_turn(user_id, req.thread_id, 0, 0, 0, kind=f"feedback_{req.rating}")
    return {"recorded": req.rating}


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


# ---------- Activity Buddy Board (opt-in, safety-first) ----------
# Identity is always server-resolved; a request body can never name whose
# post is created, updated, or decided. Domain errors map to HTTP here:
# LookupError→404, PermissionError→403, ValueError→409.

def _buddy_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


class ActivityPostCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)
    activity: str = Field(min_length=2, max_length=60)
    city: str = Field(min_length=1, max_length=60)
    area: str = Field(default="", max_length=60)
    time_window: str = Field(default="", max_length=60)
    vibe: str = Field(default="", max_length=40)
    skill_level: str = Field(default="", max_length=20)
    budget: str = Field(default="", max_length=20)
    group_size: str = Field(default="", max_length=20)
    notes: str = Field(default="", max_length=280)
    active: bool = True


class ActivityPostUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=40)
    activity: str | None = Field(default=None, min_length=2, max_length=60)
    city: str | None = Field(default=None, min_length=1, max_length=60)
    area: str | None = Field(default=None, max_length=60)
    time_window: str | None = Field(default=None, max_length=60)
    vibe: str | None = Field(default=None, max_length=40)
    skill_level: str | None = Field(default=None, max_length=20)
    budget: str | None = Field(default=None, max_length=20)
    group_size: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=280)
    active: bool | None = None


class BuddyRequestCreate(BaseModel):
    message: str = Field(default="", max_length=280)
    requester_name: str = Field(default="", max_length=40)


class BuddyRequestDecision(BaseModel):
    status: str = Field(pattern=r"^(accepted|rejected)$")


class BuddyReport(BaseModel):
    reason: str = Field(default="", max_length=280)


@app.post("/activity-posts")
async def create_activity_post(req: ActivityPostCreate, response: Response,
                               ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"post": _buddy_call(buddies.create_post, user_id, req.model_dump()),
            "safety_note": buddies.SAFETY_NOTE}


@app.get("/activity-posts")
async def search_activity_posts(response: Response, ident: Identity = Depends(),
                                activity: str | None = None, city: str | None = None,
                                time_window: str | None = None,
                                skill_level: str | None = None,
                                budget: str | None = None, vibe: str | None = None,
                                include_own: bool = False) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    posts = buddies.search_posts(user_id, activity=activity, city=city,
                                 time_window=time_window, skill_level=skill_level,
                                 budget=budget, vibe=vibe, include_own=include_own)
    return {"posts": posts, "safety_note": buddies.SAFETY_NOTE}


@app.get("/activity-posts/mine")
async def my_activity_posts(response: Response, ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"posts": buddies.my_posts(user_id)}


@app.patch("/activity-posts/{post_id}")
async def update_activity_post(post_id: int, req: ActivityPostUpdate,
                               response: Response, ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"post": _buddy_call(buddies.update_post, user_id, post_id,
                                req.model_dump(exclude_unset=True))}


@app.post("/activity-posts/{post_id}/request")
async def request_to_join(post_id: int, req: BuddyRequestCreate,
                          response: Response, ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    result = _buddy_call(buddies.create_request, user_id, post_id,
                         req.message, req.requester_name)
    return {"request": result, "safety_note": buddies.SAFETY_NOTE}


@app.get("/activity-requests/mine")
async def my_activity_requests(response: Response, ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return buddies.my_requests(user_id)


@app.patch("/activity-requests/{request_id}")
async def decide_activity_request(request_id: int, req: BuddyRequestDecision,
                                  response: Response,
                                  ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"request": _buddy_call(buddies.decide_request, user_id,
                                   request_id, req.status)}


@app.post("/activity-posts/{post_id}/report")
async def report_activity_post(post_id: int, req: BuddyReport, response: Response,
                               ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return _buddy_call(buddies.report_post, user_id, post_id, req.reason)


@app.post("/users/{public_user_key}/block")
async def block_buddy_user(public_user_key: str, response: Response,
                           ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return _buddy_call(buddies.block_user, user_id, public_user_key)


if settings().debug_endpoints:  # route does not exist unless explicitly enabled

    @app.get("/debug/state/{user_id}/{thread_id}")
    async def debug_state(user_id: str, thread_id: str,
                          trusted: bool = Depends(caller_is_trusted)) -> dict:
        """Inspect a thread: routing path + transcript. Dev-only.
        validate_startup() guarantees a token exists; require it unconditionally."""
        if not trusted:
            raise HTTPException(status_code=401, detail="token required")
        snap = await _aget_graph_state({"configurable": {"thread_id": f"{user_id}:{thread_id}"}})
        return {
            "routing_history": snap.values.get("routing_history", []),
            "message_count": len(snap.values.get("messages", [])),
            "messages": [
                {"type": m.type, "content": m.content[:200]}
                for m in snap.values.get("messages", [])
            ],
        }
