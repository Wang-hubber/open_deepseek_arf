"""multi_agent_team FastAPI server — example wiring of Team + SseRelay.

Architecture:
    POST /chat              -> routes a user message to the `pm` engine
    POST /delegate/{pool}   -> routes a user message to a subagent pool
    GET  /sse/team/{team}   -> SSE stream aggregated from every engine
    GET  /approvals         -> list pending approval requests
    POST /approve/{req_id}  -> resolve a pending approval (approve|reject)

Deferred / future framework support
-----------------------------------
The skeleton below talks to a `Team` whose `build()` returns a
placeholder with `started=True` but **no live engines or pools** (see
py-arf/src/team/team_builder.rs — TeamBuilder.build is documented as
a skeleton). The following call sites therefore degrade to a clear
"skeleton" response rather than failing at runtime:

    - `team.engine("pm")`           -> returns `None`
    - `team.subagent_pool(id)`      -> returns `None`
    - `pool.delegate(...)`          -> not reachable; the `engine`
                                       call site already returns 501
    - `SseRelay.stream(filter)`     -> returns a single-shot marker
                                       string (no per-event loop yet)

The routes, request models, lifespan wiring, and approval plumbing
are real and used today. When the framework adds
`team.engine(id) -> EngineHandle` and `pool.delegate(TaskInput) ->
TaskResult`, the `# TODO: wire to framework` markers in this file
will be the only places that need to change.

Run with:    python server.py
Then:        curl -N http://127.0.0.1:8000/sse/team/default
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# The example imports the compiled extension directly. `arf.__init__`
# re-exports the same names, so `from arf import Bus` would also work;
# we use the underscore-prefixed module so the imports map 1:1 to the
# pyclass names declared in py-arf/src/.
from arf._arf import (
    Bus,
    EventFilter,
    SseFormatter,
    SseRelay,
    TeamBuilder,
    TeamConfig,
    TeamMembership,
)

# Approval registry is app-side; the framework does not participate.
from approval import ApprovalRegistry, approvals

logger = logging.getLogger("multi_agent_team.server")

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STORAGE_ROOT = ROOT / "data" / "events"
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

TEAM_CONFIG_PATH = ROOT / "teams" / "default.yaml"

# Module-level state — populated by the lifespan handler. We keep
# these as `Optional[...]` and rebind them in `startup` because
# `on_event("startup")` is deprecated in modern FastAPI; the
# `lifespan` context manager is the supported replacement.
bus: Optional[Bus] = None
team_membership: Optional[TeamMembership] = None
sse_relay: Optional[SseRelay] = None
team: Any = None  # `py_arf.Team` once framework lands; intentionally untyped


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Boot the team on startup, tear it down on shutdown.

    Uses the modern `lifespan` API (replaces the deprecated
    `on_event("startup")` decorator used in the brief).
    """
    global bus, team_membership, sse_relay, team

    bus = Bus()  # default heartbeat + capacity from PyBus.__new__ signature
    # `bus=None` is supported by TeamMembership today (dynamic Bus
    # merge is a follow-up — see py-arf/src/relay/team_membership.rs).
    team_membership = TeamMembership(str(TEAM_CONFIG_PATH), None)

    cfg = TeamConfig.from_yaml(str(TEAM_CONFIG_PATH))
    # TeamBuilder.from_config returns a builder; `.build()` is async
    # and today produces a placeholder Team. See "Deferred" section.
    team = await TeamBuilder.from_config(bus, cfg).build()
    await team.start()

    sse_relay = SseRelay(team_membership, str(STORAGE_ROOT), buffer_size=1000)

    members = team_membership.members()
    logger.info(
        "team booted (team_id=%s, started=%s, members=%s)",
        cfg.team_id,
        team.started,
        sorted(members),
    )

    try:
        yield
    finally:
        if team is not None:
            await team.stop()
        logger.info("team stopped")


app = FastAPI(title="multi_agent_team", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatReq(BaseModel):
    message: str


class ApproveReq(BaseModel):
    approved: bool = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/chat")
async def chat(req: ChatReq):
    """Send a user message to the `pm` engine.

    TODO: wire to framework once `team.engine(id) -> EngineHandle` and
    `engine.chat(message) -> ChatResult` exist. Until then we return a
    structured 501 so callers know the wiring is intentionally not
    implemented (not a 500 / not a silent success).
    """
    if team is None:
        raise HTTPException(status_code=503, detail="team not started")

    engine = _get_engine_handle(team, "pm")
    if engine is None:
        # Deferred framework support: the skeleton's `team` has no
        # handle storage. Returning a 501 keeps the route testable.
        return {
            "status": "skeleton",
            "engine": "pm",
            "message": req.message,
            "note": "team.engine() not yet implemented in framework (Task 8 skeleton)",
        }

    result = await _engine_chat(engine, req.message)
    return {"response": result}


@app.post("/delegate/{pool_id}")
async def delegate(pool_id: str, req: ChatReq):
    """Send a user message into a named subagent pool.

    TODO: wire to framework once `team.subagent_pool(id) -> PoolHandle`
    and `pool.delegate(TaskInput) -> TaskResult` exist.
    """
    if team is None:
        raise HTTPException(status_code=503, detail="team not started")

    pool = _get_pool_handle(team, pool_id)
    if pool is None:
        return {
            "status": "skeleton",
            "pool_id": pool_id,
            "message": req.message,
            "note": "team.subagent_pool() not yet implemented in framework (Task 8 skeleton)",
        }

    result = await _pool_delegate(pool, req.message)
    # `.output` shape is brief's assumption; real TaskResult lands later.
    return {"result": getattr(result, "output", result)}


@app.get("/sse/team/{team_id}")
async def sse_team(
    team_id: str,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
):
    """Server-Sent-Events stream aggregated across the whole team.

    Accepts the standard SSE `Last-Event-ID` resume header; we parse
    it into `(node_id, event_seq)` and pass the per-node cursor to
    the filter.
    """
    if sse_relay is None:
        raise HTTPException(status_code=503, detail="sse relay not initialized")

    since: dict[str, int] = {}
    if last_event_id:
        # parse_last_event_id returns (node_id, event_seq) — note it is
        # a tuple, not Optional; missing `:` yields (s, 0).
        node_id, event_seq = SseFormatter.parse_last_event_id(last_event_id)
        since[node_id] = event_seq

    flt = EventFilter(since_event_seq=since)
    return StreamingResponse(
        _sse_stream(sse_relay, flt),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering
        },
    )


@app.get("/approvals")
def list_approvals():
    """Return all pending approval request IDs.

    Kept as a plain (non-async) handler because ApprovalRegistry is
    in-memory and CPU-only.
    """
    return {"pending": approvals.pending_ids()}


@app.get("/approvals/{request_id}")
def get_approval(request_id: str):
    entry = approvals.peek(request_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return entry


@app.post("/approve/{request_id}")
def approve(request_id: str, req: ApproveReq):
    """Resolve a pending approval.

    Returns the stored `{tool, params}` if approved, `None` if rejected.
    """
    entry = approvals.decide(request_id, req.approved)
    if entry is None and request_id not in approvals.pending_ids():
        # decide() also returns None for rejections, so we need the
        # extra membership check to disambiguate "unknown id" from
        # "rejected".
        raise HTTPException(status_code=404, detail="approval not found")
    return {"approved": req.approved, "entry": entry}


@app.get("/health")
def health():
    """Lightweight liveness check; does NOT verify team health."""
    return {"status": "ok", "team_started": bool(team and team.started)}


# ---------------------------------------------------------------------------
# Framework-shim helpers (DEFERRED — see file docstring)
# ---------------------------------------------------------------------------


def _get_engine_handle(team_obj: Any, engine_id: str) -> Any:
    """Return the persistent engine handle, or `None` if unsupported.

    TODO: replace with `team_obj.engine(engine_id)` once the framework
    exposes a real handle-storage API on `PyTeam`.
    """
    getter = getattr(team_obj, "engine", None)
    if getter is None:
        return None
    try:
        return getter(engine_id)
    except NotImplementedError:
        return None


def _get_pool_handle(team_obj: Any, pool_id: str) -> Any:
    """Return the subagent pool handle, or `None` if unsupported.

    TODO: replace with `team_obj.subagent_pool(pool_id)` once the
    framework exposes pool handle storage.
    """
    getter = getattr(team_obj, "subagent_pool", None)
    if getter is None:
        return None
    try:
        return getter(pool_id)
    except NotImplementedError:
        return None


async def _engine_chat(engine: Any, message: str) -> Any:
    """Call `engine.chat(message)`, returning a deferred stub when missing.

    TODO: drop the fallback once `Engine.chat` lands in py-arf.
    """
    chat = getattr(engine, "chat", None)
    if chat is None:
        return {
            "status": "skeleton",
            "message": message,
            "note": "Engine.chat not yet implemented in framework",
        }
    return await chat(message)


async def _pool_delegate(pool: Any, message: str) -> Any:
    """Call `pool.delegate(TaskInput)`, returning a deferred stub when missing.

    TODO: drop the fallback once `pool.delegate` lands in py-arf.
    """
    delegate = getattr(pool, "delegate", None)
    if delegate is None:
        return {
            "status": "skeleton",
            "message": message,
            "note": "Pool.delegate not yet implemented in framework",
        }
    # TaskInput is a planned, not-yet-existing type. We pass a plain
    # dict so the call is forward-compatible — the framework will
    # accept the dict form once it lands (mirrors the BaseAgent
    # convention of accepting `dict[str, Any]`).
    return await delegate({"user_message": message})


async def _sse_stream(relay: SseRelay, flt: EventFilter) -> AsyncIterator[bytes]:
    """Yield SSE-encoded bytes from the relay.

    Today's `SseRelay.stream()` is a skeleton: it returns a future of
    a String containing one `// tailer for <member>` marker per
    existing JSONL file (see py-arf/src/relay/sse_relay.rs). We
    await it and emit it as a single chunk so the wire format is
    valid SSE today; once the relay grows into a real per-event
    async iterator, the only change here is removing the `await` and
    iterating directly.
    """
    chunk = await relay.stream(flt)
    if chunk:
        # Encode as UTF-8 bytes — SSE clients require a byte stream.
        yield chunk.encode("utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    uvicorn.run(app, host="127.0.0.1", port=8000)