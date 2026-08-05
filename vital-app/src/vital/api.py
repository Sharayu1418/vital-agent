"""FastAPI entrypoint — Phase 1: full graph, threads, per-agent streaming.

Stateless (D3): thread_id keys all conversation state in the checkpointer.

Identity model (interim until real auth in Phase 5) — see security.py:
- Trusted callers (bearer token) may assert user_id.
- Anonymous callers get server-issued session cookies; their state lives
  under `anon-<session>:<thread>` — no collisions, nothing guessable.
- Debug routes exist only with DEBUG_ENDPOINTS=true, which refuses to
  boot without a token, and always require that token.
"""
import asyncio
import xml.etree.ElementTree as ET
import zipfile
from contextlib import asynccontextmanager

from fastapi import (Cookie, Depends, FastAPI, Header, HTTPException, Response,
                     UploadFile)
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from vital import buddies, guardrails, ingest, memory, metrics, storage
from vital.config import settings
from vital.graph import (build_graph_async, close_graph_resources,
                         write_memories)
from vital.security import (SESSION_COOKIE, AuthContext, authenticate,
                            caller_is_trusted, resolve_identity,
                            validate_startup)
from vital.storage import close_storage, current_user_id, initialize_storage

graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    validate_startup()      # fail closed before serving anything
    initialize_storage()    # pool + tables now, or the deploy fails here
    # graph build lives INSIDE the try, and cleanup is nested, so the pool
    # closes even when graph startup or graph cleanup raises
    try:
        graph = await build_graph_async()
        yield
    finally:
        try:
            if graph is not None:
                await close_graph_resources()
        finally:
            close_storage()


app = FastAPI(title="VITAL", version="0.5.0", lifespan=lifespan)

# Phase 5: the Next.js frontend is a separate origin. Cookies carry identity,
# so allow_credentials=True and a SINGLE explicit origin (never "*" with
# credentials — browsers reject it, and it would be wrong anyway).
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings().frontend_origin],
    # P1-12: a single allowed origin blocked every Vercel preview deployment,
    # so branch previews looked completely broken. Opt-in and anchored — see
    # the security note on preview_origin_regex in config.py.
    allow_origin_regex=settings().preview_origin_regex,
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


class Identity:
    """Dependency bundle: verified auth context + session transport
    (cookie OR mobile header). Every identity-resolving route uses this —
    one path, no per-route drift."""
    def __init__(self, auth: AuthContext = Depends(authenticate),
                 vital_session: str | None = Cookie(default=None),
                 x_vital_session: str | None = Header(default=None)):
        self.auth = auth
        self.session = vital_session or x_vital_session

    def resolve(self, req_user_id: str = "local-user") -> tuple[str, str | None]:
        return resolve_identity(req_user_id, self.auth, self.session)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str = Field(default="demo", max_length=64, pattern=r"^[\w-]+$")
    user_id: str = Field(default="local-user", max_length=64, pattern=r"^[\w-]+$")


@app.get("/healthz")
@app.get("/health")
async def healthz() -> dict:
    """Two paths on purpose.

    /healthz is registered in the OpenAPI schema and reachable in tests, but
    on Cloud Run it returns a Google-branded 404 while /openapi.json and
    /session on the same host reach the container normally — so something in
    front of the service intercepts that exact path. Cause unknown.

    /health is the alias that actually answers. If it also 404s, the
    interception is not path-specific and this comment is wrong.
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Blocking-I/O rule (P0-1 fix).
#
# storage.py is fully SYNCHRONOUS (sqlite3, or a sync psycopg ConnectionPool).
# Calling it from an `async def` handler blocks the whole event loop for the
# duration of the query — which on a single Cloud Run instance freezes every
# other in-flight request, including SSE chat streams mid-sentence.
#
# So: a handler that only does synchronous work is declared `def`, NOT
# `async def`. FastAPI runs sync handlers in a threadpool automatically, so
# the loop stays free. Handlers that genuinely need to await (the graph, the
# request body) stay `async def` and wrap their storage calls in
# run_in_threadpool().
#
# When adding a route: if it never awaits, declare it `def`.
# ---------------------------------------------------------------------------

@app.post("/auth/logout")
def logout(response: Response) -> dict:
    """Server-side half of sign-out: expire the anonymous session cookie so
    the browser doesn't keep an identity that may have been linked to the
    account. (The frontend clears Firebase + local state; security does NOT
    depend on it — resolve_identity rejects linked anonymous sessions.)"""
    cfg = settings()
    response.set_cookie(SESSION_COOKIE, "", max_age=0, httponly=True,
                        secure=cfg.session_cookie_secure,
                        samesite=cfg.session_cookie_samesite)
    return {"signed_out": True}


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


def config_for(user_id: str, thread_id: str) -> dict:
    """The checkpointer key. Identity is part of it by construction, so a
    caller can only ever address their own threads."""
    return {"configurable": {"thread_id": f"{user_id}:{thread_id}"}}


async def _screen_message(message: str, config) -> bool:
    """Crisis screen as a task, for the concurrent path. Never raises — the
    caller is a gate on user-visible output and must always get an answer."""
    try:
        history = await _recent_texts(config)
        return await run_in_threadpool(guardrails.assess, message, history)
    except Exception:
        return await run_in_threadpool(guardrails.deterministic_crisis, message)


async def _recent_texts(config, limit: int = 4) -> list[str]:
    """Last few human/ai texts on this thread, for crisis-screen context.

    'I don't want to be here anymore' is unreadable without the turns
    around it. Best-effort: a fresh thread or an unavailable checkpointer
    yields no context, and the screen still runs on the message alone.
    """
    try:
        snap = await _aget_graph_state(config)
    except Exception:
        return []
    values = getattr(snap, "values", None) or {}
    out = []
    for m in (values.get("messages") or [])[-limit:]:
        if getattr(m, "type", "") in ("human", "ai"):
            text = visible_text(getattr(m, "content", None))
            if text:
                out.append(f"{m.type}: {text}")
    return out


async def _aget_graph_state(config):
    """Read graph state from async endpoints. Async-checkpointer graphs
    (prod: AsyncPostgresSaver) require aget_state; sync/in-memory graphs
    (local dev, tests) only have get_state. Prefer async, fall back to sync."""
    if hasattr(graph, "aget_state"):
        return await graph.aget_state(config)
    return graph.get_state(config)


def _graph_stream(graph_input, config, user_id: str, screen=None):
    """Shared SSE generator for /chat and /approve. Returns the async
    generator OBJECT (callers hand it straight to EventSourceResponse).

    Besides tokens/status, emits:
    - approval_required: graph paused at request_approval (plan payload)
    - message: a final AI message that was written to state by a non-LLM
      node (commit/reject confirmations) and therefore never streamed

    `screen` is an optional awaitable resolving True when the message is a
    crisis. When present the graph runs CONCURRENTLY with it and every event
    is withheld until the verdict lands — so the classifier's ~1.5s overlaps
    the graph's own first-token latency instead of being added to it, and
    the user still never sees agent output for a crisis message.
    """
    import json as _json

    async def stream():
        import time
        t0 = time.monotonic()
        streamed_tokens = False
        streamed_chars = 0
        # P0-5: real provider counts, summed across EVERY model call in the
        # turn. Read the remaining allowance once up front so we can stop
        # mid-turn — the pre-turn check alone let one turn run unbounded.
        real_tokens = 0
        remaining = await run_in_threadpool(guardrails.remaining_budget, user_id)
        over_budget = False
        # output withheld until the crisis screen clears; discarded entirely
        # if it does not
        held: list[dict] = []
        gated = screen is not None
        crisis = False
        # run_id -> start time, so tool duration can be reported alongside
        # outcome. Popped on the matching on_tool_end.
        tool_started: dict = {}

        async def is_crisis() -> bool:
            try:
                return bool(await screen)
            except Exception:
                return False   # the screen fails safe internally

        run_config = {**config, "recursion_limit": settings().recursion_limit}
        events = graph.astream_events(graph_input, config=run_config, version="v2")
        try:
            async for event in events:
                kind = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")
                out: list[dict] = []
                if kind == "on_chat_model_stream" and node not in _NON_USER_FACING:
                    chunk = visible_text(event["data"]["chunk"].content)
                    if chunk:
                        streamed_tokens = True
                        streamed_chars += len(chunk)
                        out.append({"event": "token", "data": chunk})
                elif kind == "on_tool_start":
                    tool_started[event.get("run_id")] = time.monotonic()
                    out.append({"event": "status",
                                "data": f"{node}: using {event['name']}"})
                elif kind == "on_tool_end":
                    # Observe EVERY tool here rather than inside each tool.
                    # One place, and a new tool is covered the day it is
                    # added — nobody has to remember to instrument it.
                    started = tool_started.pop(event.get("run_id"), None)
                    outcome, detail = metrics.tool_outcome(
                        (event.get("data") or {}).get("output"))
                    metrics.log_tool(
                        user_id, event.get("name", "unknown"), outcome,
                        error=detail,
                        duration_ms=(int((time.monotonic() - started) * 1000)
                                     if started else None))
                elif kind == "on_chat_model_end":
                    real_tokens += guardrails.tokens_from_model_end(event)
                    if real_tokens >= remaining:
                        over_budget = True
                        # append rather than replace: a `message` event would
                        # clobber whatever already streamed (stream.js)
                        if streamed_tokens:
                            out.append({"event": "token",
                                        "data": "\n\n" + guardrails.OVER_BUDGET_MID_TURN})
                        else:
                            out.append({"event": "message",
                                        "data": guardrails.OVER_BUDGET_MID_TURN})

                if gated:
                    # hold everything back until the verdict is in. Checking
                    # done() rather than awaiting keeps the graph draining.
                    if screen.done():
                        if await is_crisis():
                            crisis = True
                            break
                        gated = False
                        out = held + out
                        held = []
                    else:
                        held.extend(out)
                        if over_budget:
                            break
                        continue

                for ev in out:
                    yield ev
                if over_budget:
                    break
        finally:
            # breaking out of `async for` doesn't close the generator; the
            # graph run must be torn down explicitly or it leaks a task
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                await aclose()

        # graph finished before the verdict: now we have to wait for it
        if gated and not crisis:
            if await is_crisis():
                crisis = True
            else:
                gated = False
                for ev in held:
                    yield ev
                held = []

        if crisis:
            # everything the agents produced is discarded unseen
            billed = max(1, real_tokens)
            try:
                await run_in_threadpool(guardrails.record_usage, user_id, billed)
            except Exception:
                pass
            metrics.log_turn(user_id, str(config["configurable"]["thread_id"]),
                             routing_hops=0, est_tokens=billed,
                             duration_ms=int((time.monotonic() - t0) * 1000),
                             kind="crisis_response")
            yield {"event": "message", "data": guardrails.CRISIS_RESPONSE}
            yield {"event": "done", "data": ""}
            return

        if over_budget:
            billed = max(1, real_tokens)
            try:
                await run_in_threadpool(guardrails.record_usage, user_id, billed)
            except Exception:
                pass
            metrics.log_turn(user_id, str(config["configurable"]["thread_id"]),
                             routing_hops=0, est_tokens=billed,
                             duration_ms=int((time.monotonic() - t0) * 1000),
                             kind="budget_abort")
            yield {"event": "done", "data": ""}
            return

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
        heuristic = guardrails.estimate_tokens(str(graph_input)[:2000], "x" * streamed_chars)
        # bill the provider's number when we have one; the heuristic is a
        # fallback for fakes and providers that report no usage metadata
        billed = real_tokens or heuristic
        try:
            # threadpool: a synchronous DB write here would block the event
            # loop at the exact moment other users' streams are mid-flight
            await run_in_threadpool(guardrails.record_usage, user_id, billed)
        except Exception:
            pass  # accounting must never break the stream
        routes = list(values.get("routing_history", []) or [])
        metrics.log_turn(user_id, str(config["configurable"]["thread_id"]),
                         routing_hops=len(routes),
                         est_tokens=billed,
                         duration_ms=int((time.monotonic() - t0) * 1000),
                         heuristic_tokens=heuristic,
                         routes=routes)

        yield {"event": "done", "data": ""}

        # Memory extraction runs AFTER `done`: the composer unlocks the moment
        # the answer is complete, and this model call no longer sits between
        # the answer and the user's next message. Still inside the request, so
        # Cloud Run keeps the CPU allocated (a detached background task would
        # be throttled or killed once the response finished).
        try:
            await run_in_threadpool(write_memories, user_id,
                                    values.get("messages", []) or [])
        except Exception:
            pass  # memory must never break a conversation
    return stream()  # the generator object, not the function (review fix)


@app.post("/chat")
async def chat(req: ChatRequest, ident: Identity = Depends()) -> EventSourceResponse:
    user_id, new_session = ident.resolve(req.user_id)
    current_user_id.set(user_id)  # tools read identity from here, never from the LLM
    if ident.auth.kind == "firebase":
        # signed-in users get a cross-device thread index (title = first
        # message; later turns only bump updated_at)
        await run_in_threadpool(storage.upsert_user_thread,
                                user_id, req.thread_id, req.message)

    config = config_for(user_id, req.thread_id)

    # Guardrail 1: crisis screening (P0-4). Two routes into the same check,
    # chosen by the cheap deterministic net:
    #
    # LOOKS CONCERNING -> screen FIRST, graph never starts. The original
    #   principle holds exactly: a message that already reads as distress is
    #   never routed, never hits a tool, never reaches the memory writer.
    #   Costs ~1.5s, on the small slice of messages where that is warranted.
    #
    # LOOKS ORDINARY -> screen CONCURRENTLY with the graph, holding all
    #   output until the verdict (see _graph_stream). The classifier still
    #   runs on every message, so recall is unchanged, but its latency
    #   overlaps the graph's instead of stacking on top — and the user still
    #   never sees agent output for a crisis message.
    #
    # The tradeoff, stated plainly: on a crisis message that the broad net
    # misses AND the classifier catches, the graph will have done some work
    # before being abandoned — possibly a tool call or a memory write. Rare
    # by construction (the net is tuned for recall), invisible to the user,
    # and the price of not taxing every ordinary message 1.5s.
    if guardrails.concern_signal(req.message):
        if await run_in_threadpool(guardrails.assess, req.message,
                                   await _recent_texts(config)):
            async def crisis_stream():
                yield {"event": "message", "data": guardrails.CRISIS_RESPONSE}
                yield {"event": "done", "data": ""}
            metrics.log_turn(user_id, req.thread_id, 0, 0, 0, kind="crisis_response")
            response = EventSourceResponse(crisis_stream())
            _set_session(response, new_session)
            return response
        screen = None
    else:
        screen = None  # created after the budget check, so it is never orphaned

    # Guardrail 2: per-user daily token budget
    if await run_in_threadpool(guardrails.budget_exceeded, user_id):
        raise HTTPException(status_code=429, detail=guardrails.BUDGET_MESSAGE)

    if not guardrails.concern_signal(req.message):
        screen = asyncio.create_task(_screen_message(req.message, config))

    graph_input = {"messages": [("user", req.message)], "user_id": user_id,
                   "routing_history": []}  # reset loop guard each turn
    response = EventSourceResponse(
        _graph_stream(graph_input, config, user_id, screen=screen))
    _set_session(response, new_session)
    return response


class ApprovalRequest(BaseModel):
    thread_id: str = Field(default="demo", max_length=64, pattern=r"^[\w-]+$")
    user_id: str = Field(default="local-user", max_length=64, pattern=r"^[\w-]+$")
    action: str = Field(pattern=r"^(approve|edit|reject)$")
    feedback: str = Field(default="", max_length=1000)


@app.post("/approve")
async def approve(req: ApprovalRequest,
                  ident: Identity = Depends()) -> EventSourceResponse:
    """Resume a paused plan-approval interrupt. The resume value reaches
    request_approval() exactly where interrupt() returned."""
    from langgraph.types import Command as ResumeCommand

    user_id, new_session = ident.resolve(req.user_id)
    current_user_id.set(user_id)
    config = config_for(user_id, req.thread_id)

    # /approve carries up to 1000 characters of free-text feedback and was
    # never screened (P0-4). Someone can just as easily say how they're
    # really doing while editing a plan as while chatting.
    if req.feedback.strip():
        if await run_in_threadpool(guardrails.assess, req.feedback,
                                   await _recent_texts(config)):
            async def crisis_stream():
                yield {"event": "message", "data": guardrails.CRISIS_RESPONSE}
                yield {"event": "done", "data": ""}
            metrics.log_turn(user_id, req.thread_id, 0, 0, 0, kind="crisis_response")
            response = EventSourceResponse(crisis_stream())
            _set_session(response, new_session)
            return response

    # budget applies here too: an 'edit' resume re-invokes the planner LLM,
    # so /approve must not be a budget bypass (Phase 4 review finding)
    if await run_in_threadpool(guardrails.budget_exceeded, user_id):
        raise HTTPException(status_code=429, detail=guardrails.BUDGET_MESSAGE)
    if not any(t.interrupts for t in getattr(await _aget_graph_state(config), "tasks", ())):
        raise HTTPException(status_code=409, detail="nothing awaiting approval on this thread")
    resume = ResumeCommand(resume={"action": req.action, "feedback": req.feedback})
    response = EventSourceResponse(_graph_stream(resume, config, user_id))
    _set_session(response, new_session)
    return response


MAX_UPLOAD_BYTES = 500 * 1024 * 1024
_UPLOAD_CHUNK = 1024 * 1024


async def _spool_upload(file: UploadFile):
    """Copy the upload to a temp file, aborting the moment it exceeds the cap.

    The previous code did `content = await file.read()` and checked the size
    afterwards — so a 300MB body was fully resident in memory BEFORE the 413
    could fire, and the container was OOM-killed instead of answering. Here
    the counter is checked per chunk, so an oversized upload costs one chunk
    of memory and returns a clean 413, and peak RSS stays flat regardless of
    file size.

    tempfile.TemporaryFile, not SpooledTemporaryFile: zipfile requires a
    seekable object, and SpooledTemporaryFile only grew .seekable() in 3.11.
    A real file object is always seekable, and the extra syscalls on small
    CSVs are not worth the version coupling. Deleted on close.
    """
    import tempfile

    spool = tempfile.TemporaryFile()
    size = 0
    try:
        while chunk := await file.read(_UPLOAD_CHUNK):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"file too large ({MAX_UPLOAD_BYTES // (1024 * 1024)}MB max)")
            await run_in_threadpool(spool.write, chunk)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool


@app.post("/upload/health")
async def upload_health(file: UploadFile, response: Response,
                        ident: Identity = Depends()) -> dict:
    """Apple Health export (.zip or .xml) or a sleep CSV → normalized
    per-user store. Anonymous users can upload too — their data lives under
    their session.

    NOTE for deploys: Cloud Run caps HTTP/1.1 request bodies at 32MB. This
    handler streams correctly, but the service also needs HTTP/2 enabled
    before uploads above that size can reach it at all.
    """
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    name = (file.filename or "").lower()
    spool = await _spool_upload(file)
    try:
        if name.endswith(".zip"):
            rows = await run_in_threadpool(ingest.parse_apple_health_zip, spool)
        elif name.endswith(".xml"):
            rows = await run_in_threadpool(ingest.parse_apple_health_stream, spool)
        else:
            rows = await run_in_threadpool(ingest.parse_sleep_csv, spool.read())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ET.ParseError as exc:
        raise HTTPException(status_code=422, detail=f"could not parse XML: {exc}")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="not a readable zip archive")
    finally:
        spool.close()
    await run_in_threadpool(ingest.save_sleep_data, user_id, rows)
    return {"nights_imported": len(rows),
            "date_range": [rows[0]["date"], rows[-1]["date"]]}


# ---------- Side-panel data endpoints (Phase 5 UI) ----------

@app.get("/session")
def session_bootstrap(response: Response, ident: Identity = Depends()) -> dict:
    """Establish identity in ONE request, before the client fans out (P1-9).

    Every identity-resolving route can mint a new anonymous session. The web
    app used to load sleep, calendar and memories with Promise.all, so three
    concurrent requests could each mint a DIFFERENT session id; last cookie
    written won, and anything stored under the losers was orphaned. Awaiting
    this once first means the cookie exists before anything runs in parallel.

    Returns nothing identifying — the session travels in the cookie (or the
    X-Vital-Session header for the mobile client).
    """
    _, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"ready": True}


@app.get("/sleep/recent")
def sleep_recent(response: Response, ident: Identity = Depends()) -> dict:
    """Last 14 nights, merging manual logs with uploaded data (upload wins
    on date conflicts) — feeds the side-panel trend chart."""
    user_id, new_session = ident.resolve()
    current_user_id.set(user_id)  # sleep_history reads the contextvar —
    # without this it returns whoever's identity was set last (P1 bug)
    _set_session(response, new_session)
    nights: dict[str, dict] = {}
    for row in storage.sleep_history(30):
        nights[row["log_date"]] = {"date": row["log_date"],
                                   "duration_min": row["duration_min"],
                                   "quality": row["quality"], "source": "manual"}
    for row in storage.health_rows(user_id):
        nights[row["date"]] = {"date": row["date"],
                               "duration_min": int(row["duration_min"]),
                               "quality": row["quality"] or None,
                               "source": row["source"] or "upload"}
    ordered = sorted(nights.values(), key=lambda n: n["date"])[-14:]
    return {"nights": ordered, "target_min": 480}


@app.get("/calendar")
def calendar_view(response: Response, ident: Identity = Depends()) -> dict:
    """Committed plan events — the side panel's 'Your plan' section."""
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"events": storage.calendar_events(user_id)}


@app.get("/threads")
def list_threads(response: Response, ident: Identity = Depends()) -> dict:
    """Thread index for the resolved identity — signed-in users get their
    list on any device. Never exposes user ids; only the caller's own rows.
    (Old anonymous-device thread ids can't be safely claimed by an account
    after the fact — there is no ownership proof — so they are NOT imported;
    the browser that created them keeps them in localStorage.)"""
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"threads": storage.user_threads(user_id)}


@app.delete("/threads/{thread_id}")
def delete_thread(thread_id: str, response: Response,
                  ident: Identity = Depends()) -> dict:
    """Remove a thread from the CALLER'S sidebar index (user_threads is
    keyed by the server-resolved identity, so one user can never unlist
    another's row). This does NOT erase conversation checkpoints — it's
    'remove from list', not 'delete all data'; erasing graph state is a
    separate, heavier operation."""
    if not thread_id.replace("-", "").replace("_", "").isalnum() or len(thread_id) > 64:
        raise HTTPException(status_code=422, detail="invalid thread id")
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    storage.delete_user_thread(user_id, thread_id)
    return {"removed": thread_id}


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
    snap = await _aget_graph_state(config_for(user_id, thread_id))
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
def feedback(req: FeedbackRequest, response: Response,
             ident: Identity = Depends()) -> dict:
    """Thumbs per response — the Phase 5 iteration loop. Also mirrored to
    metrics so rating trends show up next to latency/cost."""
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    storage.save_feedback(user_id, req.thread_id, req.rating, req.comment)
    metrics.log_turn(user_id, req.thread_id, 0, 0, 0, kind=f"feedback_{req.rating}")
    return {"recorded": req.rating}


@app.get("/memories")
def list_memories(response: Response, ident: Identity = Depends()) -> dict:
    """What VITAL knows about you — transparency + debugging (Phase 2B)."""
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"memories": memory.all_memories(memory.get_store(), user_id)}


@app.delete("/memories/{key}")
def delete_memory(key: str, response: Response,
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
def create_activity_post(req: ActivityPostCreate, response: Response,
                         ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"post": _buddy_call(buddies.create_post, user_id, req.model_dump()),
            "safety_note": buddies.SAFETY_NOTE}


@app.get("/activity-posts")
def search_activity_posts(response: Response, ident: Identity = Depends(),
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
def my_activity_posts(response: Response, ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"posts": buddies.my_posts(user_id)}


@app.patch("/activity-posts/{post_id}")
def update_activity_post(post_id: int, req: ActivityPostUpdate,
                         response: Response, ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"post": _buddy_call(buddies.update_post, user_id, post_id,
                                req.model_dump(exclude_unset=True))}


@app.post("/activity-posts/{post_id}/request")
def request_to_join(post_id: int, req: BuddyRequestCreate,
                    response: Response, ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    result = _buddy_call(buddies.create_request, user_id, post_id,
                         req.message, req.requester_name)
    return {"request": result, "safety_note": buddies.SAFETY_NOTE}


@app.get("/activity-requests/mine")
def my_activity_requests(response: Response, ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return buddies.my_requests(user_id)


@app.patch("/activity-requests/{request_id}")
def decide_activity_request(request_id: int, req: BuddyRequestDecision,
                            response: Response,
                            ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return {"request": _buddy_call(buddies.decide_request, user_id,
                                   request_id, req.status)}


@app.post("/activity-posts/{post_id}/report")
def report_activity_post(post_id: int, req: BuddyReport, response: Response,
                         ident: Identity = Depends()) -> dict:
    user_id, new_session = ident.resolve()
    _set_session(response, new_session)
    return _buddy_call(buddies.report_post, user_id, post_id, req.reason)


@app.post("/users/{public_user_key}/block")
def block_buddy_user(public_user_key: str, response: Response,
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
        snap = await _aget_graph_state(config_for(user_id, thread_id))
        return {
            "routing_history": snap.values.get("routing_history", []),
            "message_count": len(snap.values.get("messages", [])),
            "messages": [
                {"type": m.type, "content": m.content[:200]}
                for m in snap.values.get("messages", [])
            ],
        }
