"""Shared fixtures + helpers for Phase 6 Python E2E tests.

[构造] [方法] [边界] [时间]
"""
import asyncio
import os
import sys
import time

import pytest


# ─── Env helpers ───────────────────────────────────────────────────────

def require_minimax_key() -> str | None:
    """Read MINIMAX_API_KEY (or MINIMAX_TOKEN fallback). Return None if missing."""
    return os.environ.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_TOKEN")


# ─── Test diagnostics ──────────────────────────────────────────────────

def stage(msg: str) -> None:
    """Print a stage marker — visible in pytest -s output for diagnosis.

    Usage:
        stage("before live API call")
        response = await ...
        stage("after live API call, parsing response")
    """
    print(f"  [stage] {msg}", file=sys.stderr, flush=True)


async def wait_for_or_die(coro, *, timeout: float, label: str):
    """Wrap an awaitable in a timeout. On timeout, print what we were doing
    and raise a clear TimeoutError with the current stage.

    Usage:
        response = await wait_for_or_die(
            provider.chat(model_name=..., messages=...),
            timeout=30.0,
            label="MiniMaxProvider.chat (model=MiniMax-M3)",
        )
    """
    stage(f"start: {label} (timeout={timeout}s)")
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - t0
        print(
            f"  [TIMEOUT] {label} — no response after {elapsed:.1f}s "
            f"(limit {timeout}s)",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"  [hint]    check network or increase timeout. "
            f"Stage reached: {label}",
            file=sys.stderr,
            flush=True,
        )
        raise
    elapsed = time.perf_counter() - t0
    stage(f"done:  {label} ({elapsed:.1f}s)")
    return result


# ─── Pytest fixtures ──────────────────────────────────────────────────

@pytest.fixture
def minimax_key():
    """Skip the test if MINIMAX_API_KEY is not set."""
    key = require_minimax_key()
    if not key:
        pytest.skip("MINIMAX_API_KEY not set")
    return key


@pytest.fixture
def live_bus():
    """Fresh Bus for each test."""
    from arf import Bus
    return Bus(heartbeat_interval_ms=500, heartbeat_timeout_ms=2000, channel_capacity=32)


async def attach_live_minimax_node(bus, api_key: str, node_id_str: str):
    """Attach a real MiniMaxProvider to the bus as a ModelAdapterNode.

    Returns the node_id (NodeId). After this call, any broadcast
    `model_call` on the bus will be picked up by the ModelAdapterNode
    and forwarded to the real MiniMax API. This is the missing piece in
    the original live Engine tests — without it, Engine.run() broadcasts
    `model_call` and hangs forever (no responder).

    Usage:
        node_id = await attach_live_minimax_node(
            bus=live_bus, api_key=minimax_key,
            node_id_str="model/e2e-text",
        )
        # Now build engine with agent_id="e2e-text" — it will route
        # model_call to model/e2e-text which is the live node.

    Implementation note: `MiniMaxConfig.api_key` is a read-only attribute
    in the current binding (no setter exposed). We use `from_env()` which
    reads `MINIMAX_API_KEY` (or `MINIMAX_TOKEN` fallback). The caller
    is responsible for setting that env var before calling this helper.
    The `api_key` parameter is checked (and re-set into env if needed)
    to keep the helper API explicit.
    """
    from arf import (
        MiniMaxConfig, MiniMaxProvider, NodeId, NodeInfo, MessageFilter, ToMatch,
    )

    # If api_key is provided but not in env, set it for the duration of
    # the test (don't pollute other tests — use monkeypatching at the
    # call site if persistence is needed).
    if api_key and not os.environ.get("MINIMAX_API_KEY"):
        os.environ["MINIMAX_API_KEY"] = api_key
        try:
            cfg = MiniMaxConfig.from_env()
        finally:
            os.environ.pop("MINIMAX_API_KEY", None)
    else:
        cfg = MiniMaxConfig.from_env()

    # `from_env` already set timeout — leave as is.
    provider = MiniMaxProvider(cfg)

    node_id = NodeId(node_id_str)
    # provider.connect_to_bus() does bus.connect() internally — do NOT
    # pre-register or you get "node already connected" error.
    #
    # The return value of connect_to_bus() can be safely discarded —
    # the provider holds a clone of the ModelAdapterNode in its
    # `connected_nodes` Arc<Mutex<Vec<...>>>, keeping the listen loop
    # alive for the provider's lifetime. This is a framework-side
    # fix for the silent GC-death bug (Phase 6 follow-up 6.22.5).
    await provider.connect_to_bus(bus=bus, node_id=node_id)
    return node_id


@pytest.fixture(autouse=True)
def _test_diagnostics(request):
    """Per-test start/end markers + soft wall-clock warning.

    Soft warning at 25s helps catch slow tests without killing them. The
    real kill switch is `asyncio.wait_for` inside each test (or a shell
    `timeout 90 pytest ...` invocation).
    """
    name = request.node.name
    t0 = time.perf_counter()
    print(f"\n[test] {name} — start", file=sys.stderr, flush=True)
    yield
    elapsed = time.perf_counter() - t0
    flag = " ⚠ SLOW" if elapsed > 25 else ""
    print(
        f"[test] {name} — end ({elapsed:.1f}s){flag}",
        file=sys.stderr,
        flush=True,
    )