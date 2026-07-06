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
    NodeInfo,
    SseFormatter,
    SseRelay,
    TeamBuilder,
    TeamConfig,
    TeamMembership,
)

# Approval registry is app-side; the framework does not participate.
from approval import ApprovalRegistry, approvals

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

    # Register a model node on the bus so EngineBuilder.build() can
    # resolve its Strict route (Task 14: real engine wiring).
    # Real apps typically register a ModelAdapter node here; for
    # the example we publish a minimal node entry that satisfies
    # the lookup. Actual LLM calls flow through arf-model-adapter
    # registered separately by the AgentConfig loaders.
    await bus.connect(
        NodeInfo(
            f"model/{provider['name']}",
            "model",
            {
                "provider": provider["name"],
                "kind": "model",
                "models": [provider["default_model"]],
            },
        ),
        MessageFilter(),
    )

    cfg = TeamConfig.from_yaml(str(TEAM_CONFIG_PATH))
    # Real wiring: build constructs Engine + SubagentPool per spec.
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