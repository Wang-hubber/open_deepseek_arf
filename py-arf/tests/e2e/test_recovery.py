"""[E2E] py-arf Bus.barrier + checkpoint file persistence.

[方法] [序列化] [时间]
"""
import asyncio
import json
import uuid
from pathlib import Path

import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch
from arf._arf import AgentConfig, EngineBuilder, EngineState, Route
from .conftest import attach_live_minimax_node, stage, wait_for_or_die

LIVE_TIMEOUT = 30.0
BARRIER_TIMEOUT = 3.0


@pytest.mark.asyncio
async def test_python_bus_barrier_returns_all_acked(live_bus, minimax_key):
    """[方法] Bus-side barrier handshake exercised end-to-end (offline)."""
    stage("connect responder + sender")
    responder = await live_bus.connect(
        info=NodeInfo("node/responder", "test", {}),
        filter=MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    sender = await live_bus.connect(
        info=NodeInfo("test/sender", "test", {}),
        filter=MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    cid = str(uuid.uuid4())
    stage(f"send barrier_request (cid={cid[:8]}...)")
    await sender.send(
        msg_type="barrier_request", to=[], payload={"correlation_id": cid}
    )

    received_request = None
    end = asyncio.get_event_loop().time() + BARRIER_TIMEOUT
    while asyncio.get_event_loop().time() < end:
        try:
            m = await asyncio.wait_for(responder.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        if m.msg_type == "barrier_request":
            received_request = m
            break
    stage(f"received_request = {received_request is not None}")
    assert received_request is not None, (
        f"responder did not observe barrier_request within {BARRIER_TIMEOUT}s"
    )
    assert received_request.payload.get("correlation_id") == cid

    stage("reply with barrier_ack")
    await responder.send(
        msg_type="barrier_ack", to=[], payload={"correlation_id": cid}
    )


@pytest.mark.asyncio
async def test_python_checkpoint_writes_and_reads_state_file(
    live_bus, minimax_key, tmp_path
):
    """[序列化] state.messages round-trips through JSON file (live path)."""
    stage("attach live MiniMax node on bus at model/e2e-checkpoint")
    await attach_live_minimax_node(
        bus=live_bus, api_key=minimax_key, node_id_str="model/e2e-checkpoint"
    )

    config = AgentConfig(
        provider="minimax",
        model="MiniMax-M3",
        routes={"model_call": Route.strict(ids=[NodeId("model/e2e-checkpoint")])},
    )
    stage("build engine")
    engine = await wait_for_or_die(
        EngineBuilder.new([live_bus]).build(config),
        timeout=LIVE_TIMEOUT,
        label="EngineBuilder.build(MiniMax-M3)",
    )
    state = EngineState()
    stage("engine.run('Reply with just the word: CHECKPOINT')")
    output = await wait_for_or_die(
        engine.run(state, "Reply with just the word: CHECKPOINT"),
        timeout=LIVE_TIMEOUT,
        label="Engine.run → MiniMax-M3 (checkpoint test)",
    )
    stage(f"output = {output!r}")

    # 2026-07-02: system prefix is no longer stored in state.messages
    # (mirrors react_loop.rs::react_single_round_text).
    assert len(state.messages) >= 2, (
        f"expected ≥2 messages, got {len(state.messages)}: "
        f"{[m['role'] for m in state.messages]}"
    )

    out_path = Path(tmp_path) / "checkpoint.json"
    stage(f"write state.messages → {out_path}")
    out_path.write_text(json.dumps(state.messages, indent=2))

    stage(f"read back from {out_path}")
    loaded = json.loads(out_path.read_text())
    assert isinstance(loaded, list)
    assert len(loaded) == len(state.messages)
    # 2026-07-02: system prefix is no longer stored in state.messages
    # (mirrors react_loop.rs::react_single_round_text).
    assert loaded[0]["role"] == "user"
    assert loaded[1]["role"] == "assistant"
    assert output.strip() and output.strip() in loaded[-1]["content"], (
        f"loaded assistant content {loaded[-1]['content']!r} should "
        f"contain engine output {output!r}"
    )
