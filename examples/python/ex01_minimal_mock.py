"""Minimal Engine run with a mock model node (no API key required).

Demonstrates:
  - Bus construction
  - Mock model node attach (subscribe to model_call, reply with canned text)
  - AgentConfig + EngineBuilder.new().build()
  - engine.run() with EngineState
  - State inspection after run

Run:
  .venv/bin/python py-arf/python/arf/examples/ex01_minimal_mock.py

Expected output: round_count=1, turn_count=1, single assistant message.
"""

import asyncio
import time
from arf import (
    Bus,
    NodeId,
    NodeInfo,
    MessageFilter,
    AgentConfig,
    EngineBuilder,
    EngineState,
)


async def attach_mock_model(bus, text):
    """Attach a one-shot mock model node. Returns the handle."""
    mock = await bus.connect(
        info=NodeInfo(
            node_id="model/mock",
            node_type="model",
            capabilities={"provider": "mock", "models": ["mock-v1"]},
        ),
        filter=MessageFilter(types=["model_call"]),
    )

    async def responder():
        while True:
            try:
                msg = await mock.recv()
                cid = msg.payload.get("correlation_id") if isinstance(msg.payload, dict) else None
                await mock.send(
                    msg_type="model_response",
                    to=[msg.sender],
                    payload={
                        "correlation_id": cid,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    },
                )
            except Exception:
                break

    return mock, asyncio.create_task(responder())


async def main():
    t0 = time.perf_counter()
    bus = Bus()
    mock, task = await attach_mock_model(bus, text="pong from mock")

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(agent_id="ex01-minimal", provider="mock", model="mock-v1"),
    )
    state = EngineState()
    output = await engine.run(state=state, user_input="hello")

    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"output={output!r}")
    print(f"state.round_count={state.round_count}, state.turn_count={state.turn_count}")
    print(f"messages: {[(m['role'], m['content'][:40]) for m in state.messages]}")
    print(f"elapsed={elapsed_ms:.1f}ms")

    task.cancel()
    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())