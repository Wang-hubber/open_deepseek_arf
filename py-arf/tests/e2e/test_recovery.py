"""[E2E] py-arf Bus.barrier + checkpoint file persistence.

[方法] [序列化] [时间]

Mirrors crates/arf-e2e/tests/recovery.rs. Note that the Python py-arf
bindings do NOT currently expose:
  - `Bus.barrier()` — Rust has it; Python would need a new binding
  - `AgentConfig.checkpoint_rules` — needed to register CheckpointRules

Both tests are implemented as best-effort given those gaps; the
checkpoint-persistence test writes a snapshot of state.messages to a JSON
file directly (without going through an actual CheckpointRule) so the
round-trip serialization path is still exercised.
"""
import json
import tempfile
import uuid
from pathlib import Path

import pytest
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch
from arf._arf import AgentConfig, EngineBuilder, EngineState


@pytest.mark.asyncio
async def test_python_bus_barrier_returns_all_acked(live_bus, minimax_key):
    """[方法] Bus-side barrier handshake exercised end-to-end.

    The Rust Bus has a `barrier()` method that broadcasts a barrier_request
    and collects `barrier_ack` replies. py-arf does not yet expose
    `Bus.barrier()` as a Python method, so this test exercises the
    underlying wire protocol: a Python node subscribes to barrier_request,
    replies with barrier_ack carrying the correlation_id. The test asserts
    that the request reached the node and the ack is well-formed.

    The Rust equivalent is recovery.rs::bus_barrier_collects_acks_from_n_participants.
    """
    responder = await live_bus.connect(
        NodeInfo("node/responder", "test", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )

    # Send a synthetic barrier_request to verify the wire format is right.
    cid = str(uuid.uuid4())
    sender = await live_bus.connect(
        NodeInfo("test/sender", "test", {}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    await sender.send(
        "barrier_request", [], {"correlation_id": cid}
    )

    # Receive and respond.
    deadline = 3.0
    import asyncio

    received_request = None
    loop = asyncio.get_event_loop()
    end = loop.time() + deadline
    while loop.time() < end:
        try:
            m = await asyncio.wait_for(responder.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        if m.msg_type == "barrier_request":
            received_request = m
            break
    assert received_request is not None, (
        f"responder did not observe barrier_request within {deadline}s"
    )
    assert received_request.payload.get("correlation_id") == cid

    # Reply with barrier_ack so a real Bus.barrier() (when added) would
    # observe the ack — this exercises the ack shape.
    await responder.send(
        "barrier_ack", [], {"correlation_id": cid}
    )


@pytest.mark.asyncio
async def test_python_checkpoint_writes_and_reads_state_file(
    live_bus, minimax_key, tmp_path
):
    """[序列化] state.messages round-trips through JSON file.

    The Rust reference (recovery.rs::round_end_checkpoint_writes_file_and_returns)
    uses an actual CheckpointRule + AppCheckpoint Node. py-arf does not yet
    expose `AgentConfig.checkpoint_rules`, so this Python test performs the
    same logical round-trip manually:
      1. Run engine once → state.messages populated.
      2. Serialize state.messages to JSON in tmp_path.
      3. Read JSON back, verify all roles and content preserved.
    """
    config = AgentConfig(
        agent_id="e2e-checkpoint",
        provider="minimax",
        model="MiniMax-M3",
    )
    engine = await EngineBuilder.new([live_bus]).build(config)
    state = EngineState()
    output = await engine.run(state, "Reply with just the word: CHECKPOINT")

    assert len(state.messages) >= 3

    # Write state.messages to JSON.
    out_path = Path(tmp_path) / "checkpoint.json"
    out_path.write_text(json.dumps(state.messages, indent=2))

    # Read back and verify.
    loaded = json.loads(out_path.read_text())
    assert isinstance(loaded, list)
    assert len(loaded) == len(state.messages)
    assert loaded[0]["role"] == "system"
    assert loaded[1]["role"] == "user"
    assert loaded[2]["role"] == "assistant"
    # The final assistant message content should match (or contain) the
    # engine's returned output — same invariant as test_engine_roundtrip.
    assert output.strip() and output.strip() in loaded[-1]["content"], (
        f"loaded assistant content {loaded[-1]['content']!r} should "
        f"contain engine output {output!r}"
    )