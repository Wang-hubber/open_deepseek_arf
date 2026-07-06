"""multi_agent_team FastAPI server — example wiring of Team + SseRelay.

Architecture:
    POST /chat                       -> routes a user message to the `pm` engine
    POST /delegate/{pool}            -> routes a user message to a subagent pool
    GET  /sse/team/{team}            -> SSE stream aggregated from every engine
    GET  /approvals                  -> list pending approval requests
    POST /approve/{req_id}           -> resolve a pending approval (approve|reject)
    GET  /stats/engine/{engine_id}   -> per-engine round/model/tool stats
    GET  /stats/team/{team_id}       -> team-session rollup
    GET  /stats/session/{session_id} -> single session stats (alias to engine)

Framework wiring (Task 14)
--------------------------
This server talks to a `Team` whose `build()` now constructs real
`Engine`s and `SubagentPool`s from the agent YAMLs in `teams/*.yaml`
(see py-arf/src/team/team_builder.rs). The endpoints therefore make
real LLM calls when the configured providers have credentials; tests
that exercise the routes (see tests/test_basic_flow.py) skip them
when the required provider key is not set in the environment.

Endpoints in detail:

    POST /chat
        Resolves `team.engine("pm") -> EngineHandle` and awaits
        `engine.chat(message) -> str` (single-turn chat). The
        EngineHandle is a PyO3 wrapper around the underlying
        `Arc<TokioMutex<Engine>>`, so concurrent /chat calls are
        serialized through the engine's mutex.

    POST /delegate/{pool_id}
        Resolves `team.subagent_pool(pool_id) -> PoolHandle` and
        awaits `pool.delegate({user_message}) -> {output, ...}`.
        The pool's slot is provisioned lazily on the first
        `delegate()` call (Task 14 limitation — see team_builder.rs).

    GET /stats/{engine|team|session}/{id}
        TokenStats aggregator over per-engine JSONL files. On-demand
        scan (no cache). See `stats.py` for the data model.

Run with:    ARF_PROVIDER={deepseek|aliyun_bailian|minimax} \\
             {DEEPSEEK|DASHSCOPE|MINIMAX}_API_KEY=sk-... python server.py
"""

from __future__ import annotations

import logging
import os
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
    MessageFilter,
    NodeId,
    NodeInfo,
    SseFormatter,
    SseRelay,
    TeamBuilder,
    TeamConfig,
    TeamMembership,
    ToMatch,
)

# Live provider factories — Task 18d §5.1 wiring. The example app
# wires a real ModelAdapter on the bus so engine `model_call`s reach
# the upstream API. Server.py registers a single provider per boot;
# switching providers requires ARF_PROVIDER + matching API key env.
from arf._arf import (
    AnthropicConfig,
    AnthropicProvider,
    DeepSeekConfig,
    DeepSeekProvider,
    MiniMaxConfig,
    MiniMaxProvider,
    OpenAIConfig,
    OpenAIProvider,
)

# Approval registry is app-side; the framework does not participate.
from approval import ApprovalRegistry, approvals

# Issue 2 wiring — PythonToolNode (in-process MCP node hosting the
# example's `tools/<name>/{tool.yaml,function.py}` defs) +
# PermissionRequestHandlerNode (bridges engine.permission_request ↔
# ApprovalRegistry ↔ /approve HTTP endpoint).
from tool_nodes import PythonToolNode, PermissionRequestHandlerNode

# TokenStats aggregator (Task 18c). Import after sys.path tweak below.
from stats import aggregate_engine, aggregate_team, team_rollup

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

# Live bus → SSE bridge. The example installs a JsonlSessionStore-
# free `SseRelay` that depends on per-engine JSONL files, but the
# team builder doesn't wire a session store in `server.py`, so
# `data/events/` stays empty. Instead we open a separate "spy" node
# on the bus and forward every bus message to a Python-side queue;
# the `/sse/team/{team_id}` handler drains that queue. This makes
# the SSE feed work without changing the framework wiring.
import asyncio as _asyncio
import json as _json
_live_event_queue: "_asyncio.Queue[dict]" = _asyncio.Queue()
_bus_spy_task: Optional[_asyncio.Task] = None

# Issue 2 — bus actor nodes. Populated at lifespan startup.
_tool_node: Optional[PythonToolNode] = None
_permission_handler: Optional[PermissionRequestHandlerNode] = None


# ── Provider validation (Task 18d §5.1) ────────────────────────────────────
#
# ARF requires an EXPLICIT `ARF_PROVIDER` env var (no auto-fallback). The
# chosen provider must have its `*_API_KEY` env var set, otherwise startup
# fails fast with a clear error message.

_PROVIDERS = {
    "deepseek": {
        "env_var": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "aliyun_bailian": {
        "env_var": "DASHSCOPE_API_KEY",
        "default_model": "qwen3-max",
    },
    "minimax": {
        "env_var": "MINIMAX_API_KEY",
        "default_model": "MiniMax-Text-01",
    },
}


def _resolve_provider() -> dict:
    """Fail-fast validation. Raises RuntimeError if ARF_PROVIDER is
    missing, unknown, or has no matching API key."""
    name = os.environ.get("ARF_PROVIDER")
    if not name:
        raise RuntimeError(
            "ARF_PROVIDER env var not set. "
            f"Choose one of: {sorted(_PROVIDERS.keys())}. "
            "See pricing.example.yaml for endpoints."
        )
    if name not in _PROVIDERS:
        raise RuntimeError(
            f"ARF_PROVIDER={name!r} not recognized. "
            f"Valid options: {sorted(_PROVIDERS.keys())}"
        )
    p = _PROVIDERS[name]
    api_key = os.environ.get(p["env_var"])
    if not api_key:
        raise RuntimeError(
            f"ARF_PROVIDER={name} requires env var {p['env_var']} to be set."
        )
    return {"name": name, **p, "api_key": api_key}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Boot the team on startup, tear it down on shutdown.

    Uses the modern `lifespan` API (replaces the deprecated
    `on_event("startup")` decorator used in the brief).
    """
    global bus, team_membership, sse_relay, team

    # Provider validation — fail fast with a clear error.
    provider = _resolve_provider()
    logger.info(
        "provider resolved: name=%s model=%s", provider["name"], provider["default_model"]
    )

    bus = Bus()  # default heartbeat + capacity from PyBus.__new__ signature
    # `bus=None` is supported by TeamMembership today (dynamic Bus
    # merge is a follow-up — see py-arf/src/relay/team_membership.rs).
    team_membership = TeamMembership(str(TEAM_CONFIG_PATH), None)

    # Register a real ModelAdapter so engine `model_call` messages
    # reach the upstream LLM. We pick the provider based on
    # ARF_PROVIDER (validated above) and use the matching config
    # factory. The node_id is `model/<provider>` so each engine's
    # registry lookup (provider match) finds it.
    provider_name = provider["name"]
    api_key = provider["api_key"]
    default_model = provider["default_model"]
    if provider_name == "deepseek":
        model_provider = DeepSeekProvider(
            DeepSeekConfig(api_key=api_key, models=[default_model])
        )
    elif provider_name == "minimax":
        # MiniMaxConfig exposes api_key/models as read-only getters —
        # `from_env()` populates api_key from `MINIMAX_API_KEY` and
        # models from the canonical default (`MiniMax-M3`). Use as-is.
        model_provider = MiniMaxProvider(MiniMaxConfig.from_env())
        actual_model = model_provider.supported_models[0]
    elif provider_name == "aliyun_bailian":
        model_provider = OpenAIProvider(
            OpenAIConfig(
                api_key=api_key,
                models=[default_model],
                endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            )
        )
    elif provider_name == "anthropic":
        model_provider = AnthropicProvider(
            AnthropicConfig(api_key=api_key, models=[default_model])
        )
    else:
        raise RuntimeError(f"unhandled provider {provider_name!r}")
    await model_provider.connect_to_bus(bus, NodeId(f"model/{provider_name}"))

    # Reconcile the published default_model: when providers expose a
    # canonical model name via `supported_models()`, prefer it so the
    # bus node's `models` list aligns with the agent yaml.
    if provider_name == "minimax":
        default_model = actual_model
        provider["default_model"] = default_model
    # `connect_to_bus` above already registered `model/<provider>` as a
    # real model node on the bus, so no additional `bus.connect()`
    # placeholder is needed — that would collide on the same node_id.

    cfg = TeamConfig.from_yaml(str(TEAM_CONFIG_PATH))
    # Issue 2 — PythonToolNode (MCP dispatcher for tools/<name>/)
    # and PermissionRequestHandlerNode (bridges engine.permission_request
    # ↔ ApprovalRegistry + /approve). Both register as bus actors
    # BEFORE team.start() so the engine's ResourceRegistry can resolve
    # the `mcp/workspace` node (Strict route lookup).
    global _tool_node, _permission_handler
    workspace_root = ROOT / "shared_workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)
    _tool_node = PythonToolNode(
        bus,
        tools_dir=ROOT / "tools",
        workspace_root=workspace_root,
    )
    await _tool_node.start()
    _permission_handler = PermissionRequestHandlerNode(
        bus, registry=approvals,
    )
    await _permission_handler.start()

    # Real wiring: build constructs Engine + SubagentPool per spec.
    team = await TeamBuilder.from_config(bus, cfg).build()
    await team.start()

    # ── Bus-actor wiring: every SubagentPool listens for subagent_delegate ──
    # Issue 1 fix: with the pool as a bus actor, /delegate/<pool_id>
    # sends a `subagent_delegate` message on the bus and awaits a
    # `subagent_result` reply. This replaces the pre-bus-actor direct
    # `pool.delegate(...)` call (which broke with spawn_local panic).
    pool_ids = [pp.pool_id for pp in cfg.subagent_pools]
    for pool in (team.subagent_pool(pid) for pid in pool_ids):
        if pool is None:
            continue
        nid = await pool.connect_to_bus(pool.pool_id)
        logger.info(
            "subagent_pool bus-actor wired (pool_id=%s node_id=%s)",
            pool.pool_id, nid,
        )

    sse_relay = SseRelay(team_membership, str(STORAGE_ROOT), buffer_size=1000)

    # ── Live bus → SSE bridge ────────────────────────────────────────
    # Register a `spy` node that receives every message on the bus
    # (ToMatch.All + types=None means all msg_types), then forward
    # everything to an asyncio.Queue. The /sse handler drains it.
    global _bus_spy_task
    spy_handle = await bus.connect(
        NodeInfo(
            node_id="spy/sse-bridge",
            node_type="observer",
            capabilities={"kind": "observer"},
            online_since=0,
        ),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    async def _spy_loop() -> None:
        seq = 0
        while True:
            try:
                msg = await spy_handle.recv()
            except Exception as e:  # bus closed during shutdown
                logger.info("spy_loop exiting: %s", e)
                return
            seq += 1
            try:
                payload = msg.payload
                if hasattr(payload, "items"):
                    payload_json = _json.dumps(dict(payload), default=str)
                else:
                    payload_json = str(payload)
            except Exception:
                payload_json = "{}"
            _live_event_queue.put_nowait({
                "seq": seq,
                "msg_type": msg.msg_type,
                "sender": str(msg.sender),
                "to": [str(x) for x in msg.to],
                "correlation_id": str(msg.id),
                "broadcast": bool(msg.is_broadcast() if callable(getattr(msg, "is_broadcast", None)) else msg.is_broadcast),
                "ts": msg.timestamp,
                "payload_json": payload_json,
            })

    _bus_spy_task = _asyncio.create_task(_spy_loop())
    logger.info("live SSE bridge started (spy node connected)")

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
    """Send a user message to the `pm` engine."""
    if team is None:
        raise HTTPException(status_code=503, detail="team not started")

    engine = team.engine("pm")
    if engine is None:
        # team was built without a 'pm' engine — configuration issue.
        raise HTTPException(status_code=404, detail="engine 'pm' not in team roster")

    response = await engine.chat(req.message)
    return {"response": response}


@app.post("/delegate/{pool_id}")
async def delegate(pool_id: str, req: ChatReq):
    """Send a user message into a named subagent pool."""
    if team is None:
        raise HTTPException(status_code=503, detail="team not started")

    pool = team.subagent_pool(pool_id)
    if pool is None:
        raise HTTPException(
            status_code=404, detail=f"subagent pool '{pool_id}' not in team roster"
        )

    result = await pool.delegate({"user_message": req.message})
    # result is a Python dict with `output` / `turns_consumed` /
    # `pending_peer_messages` keys (see PyPoolHandle::delegate).
    return {"result": result}


@app.get("/sse/team/{team_id}")
async def sse_team(
    team_id: str,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
):
    """Server-Sent-Events stream aggregated across the whole team.

    Drains the live bus → queue bridge populated by the spy node
    registered at lifespan startup. Each queued event is encoded with
    `SseFormatter.format_message()` (id = `<sender>:<seq>`).

    `Last-Event-ID` resume: we parse the last-seen id (`<sender>:<seq>`),
    skip queued events up to that seq for the matching sender, and
    forward the rest.
    """
    cursor: Optional[tuple[str, int]] = None
    if last_event_id:
        parsed = SseFormatter.parse_last_event_id(last_event_id)
        if parsed is not None:
            cursor = (str(parsed[0]), int(parsed[1]))

    async def gen():
        global _live_event_queue
        # Drain queued events up to ~10 minutes; clients can reconnect.
        deadline = _asyncio.get_event_loop().time() + 600
        while _asyncio.get_event_loop().time() < deadline:
            try:
                evt = await _asyncio.wait_for(_live_event_queue.get(), timeout=1.0)
            except _asyncio.TimeoutError:
                # Keep-alive heartbeat so proxies don't drop the connection.
                yield b": keep-alive\n\n"
                continue
            sender = evt["sender"]
            seq = evt["seq"]
            if cursor and sender == cursor[0] and seq <= cursor[1]:
                continue  # already delivered
            payload = {
                "msg_type": evt["msg_type"],
                "sender": sender,
                "to": evt["to"],
                "correlation_id": evt["correlation_id"],
                "broadcast": evt["broadcast"],
                "ts": evt["ts"],
                "payload_json": evt["payload_json"],
            }
            data = _json.dumps(payload, ensure_ascii=False)
            chunk = SseFormatter.format(data, seq, evt["msg_type"])
            yield chunk.encode("utf-8") + b"\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
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
async def approve(request_id: str, req: ApproveReq):
    """Resolve a pending approval.

    The endpoint wakes the PermissionRequestHandlerNode's matching
    `asyncio.Event`, which causes it to send a `permission_response`
    reply on the bus and unblock the engine's `request_permission()`
    call. The handler manages its own bookkeeping; the ApprovalRegistry
    is the side channel the HTTP frontend reads from
    (`GET /approvals`, `GET /approvals/{id}`).
    """
    if _permission_handler is None:
        raise HTTPException(status_code=503, detail="permission handler not started")
    entry = _permission_handler.decide(request_id, req.approved)
    if entry is None and not req.approved:
        # Rejection — handler dropped the entry from its map after the
        # engine receives the response. Surface a 200 anyway.
        return {"approved": req.approved, "entry": None}
    if entry is None and request_id not in approvals.pending_ids():
        raise HTTPException(status_code=404, detail="approval not found")
    return {"approved": req.approved, "entry": entry}


@app.get("/stats/engine/{engine_id}")
def engine_stats(engine_id: str):
    """Scan events.{engine_id}.jsonl, return per-engine stats."""
    p = STORAGE_ROOT / f"events.{engine_id}.jsonl"
    s = aggregate_engine(p, engine_id)
    return {
        "engine_id": s.engine_id,
        "rounds": {
            "total": s.rounds.total_rounds,
            "total_duration_ms": s.rounds.total_duration_ms,
            "avg_duration_ms": (
                s.rounds.total_duration_ms / s.rounds.total_rounds
                if s.rounds.total_rounds else 0.0
            ),
            "by_round": s.rounds.by_round,
        },
        "model_calls": {
            "total_calls": s.model_calls.total_calls,
            "input_tokens": s.model_calls.total_input_tokens,
            "output_tokens": s.model_calls.total_output_tokens,
            "total_tokens": s.model_calls.total_tokens,
            "by_model": s.model_calls.by_model,
        },
        "tool_calls": {
            "total_calls": s.tool_calls.total_calls,
            "success": s.tool_calls.success_count,
            "failure": s.tool_calls.failure_count,
            "failure_rate": (
                s.tool_calls.failure_count / s.tool_calls.total_calls
                if s.tool_calls.total_calls else 0.0
            ),
            "avg_duration_ms": (
                s.tool_calls.total_duration_ms / s.tool_calls.total_calls
                if s.tool_calls.total_calls else 0.0
            ),
            "by_tool": s.tool_calls.by_tool,
        },
        "peer_messages_sent": s.peer_messages_sent,
        "peer_messages_received": s.peer_messages_received,
    }


@app.get("/stats/team/{team_id}")
def team_stats(team_id: str):
    """Sum all engines in team → rollup."""
    if team_membership is None:
        raise HTTPException(status_code=503, detail="team not started")
    members = list(team_membership.members())
    per_engine = aggregate_team(STORAGE_ROOT, team_id, members)
    return {"team_id": team_id, "members": members, **team_rollup(per_engine)}


@app.get("/stats/session/{session_id}")
def session_stats(session_id: str):
    """Single session stats — session_id typically equals engine_id
    (in the current framework session = engine)."""
    return engine_stats(session_id)


@app.get("/health")
def health():
    """Lightweight liveness check; does NOT verify team health."""
    return {"status": "ok", "team_started": bool(team and team.started)}


# ---------------------------------------------------------------------------
# Framework-shim helpers (DEFERRED — see file docstring)
# ---------------------------------------------------------------------------


async def _sse_stream(relay: SseRelay, flt: EventFilter) -> AsyncIterator[bytes]:
    """Yield SSE-encoded bytes from the relay.

    `SseRelay.stream()` returns an async iterator that merges JSONL
    events from every team member's `events.<member>.jsonl` file
    (see py-arf/src/relay/sse_relay.rs). We iterate it directly.
    """
    stream = relay.stream(flt)
    async for chunk in stream:
        if chunk:
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