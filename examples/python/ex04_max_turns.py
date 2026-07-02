"""max_turns truncation: prevent infinite tool-call loops.

Demonstrates:
  - Mock model always returns tool_calls (infinite loop scenario)
  - Engine raises after turn_count == max_turns
  - Caller can recover the last assistant text from state.messages

Run: .venv/bin/python py-arf/python/arf/examples/ex04_max_turns.py
"""

import asyncio
import time
from arf import (
    Bus,
    NodeInfo,
    MessageFilter,
    AgentConfig,
    EngineBuilder,
    EngineState,
)


async def attach_always_tool_call_model(bus):
    mock = await bus.connect(
        info=NodeInfo(
            node_id="model/mock",
            node_type="model",
            capabilities={"provider": "mock", "models": ["mock-v1"]},
        ),
        filter=MessageFilter(types=["model_call"]),
    )

    async def responder():
        n = 0
        while True:
            try:
                msg = await mock.recv()
                cid = msg.payload.get("correlation_id") if isinstance(msg.payload, dict) else None
                n += 1
                await mock.send(
                    msg_type="model_response",
                    to=[msg.sender],
                    payload={
                        "correlation_id": cid,
                        "message": {"role": "assistant", "content": ""},
                        "tool_calls": [{
                            "id": f"call_{n}",
                            "name": "noop",
                            "arguments": {},
                        }],
                        "finish_reason": "tool_calls",
                    },
                )
            except Exception:
                break

    return mock, asyncio.create_task(responder())


async def main():
    t0 = time.perf_counter()
    bus = Bus()
    mock, model_task = await attach_always_tool_call_model(bus)

    mcp = await bus.connect(
        info=NodeInfo(
            node_id="mcp/noop",
            node_type="mcp",
            capabilities={"tools": [{"name": "noop"}]},
        ),
        filter=MessageFilter(types=["tool_exec"]),
    )

    async def tool_resp():
        while True:
            try:
                msg = await mcp.recv()
                cid = msg.payload.get("correlation_id") if isinstance(msg.payload, dict) else None
                await mcp.send(
                    msg_type="tool_result",
                    to=[msg.sender],
                    payload={"correlation_id": cid, "content": "ok", "ok": True},
                )
            except Exception:
                break

    tool_task = asyncio.create_task(tool_resp())

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            provider="mock",
            model="mock-v1",
            max_turns=2,
        ),
    )
    state = EngineState()
    try:
        out = await engine.run(state=state, user_input="infinite loop please")
        print(f"unexpectedly got output: {out!r}")
    except Exception as e:
        print(f"engine raised after turn_count={state.turn_count}: {e}")
        print(f"messages count={len(state.messages)}")
        print(f"elapsed={(time.perf_counter() - t0) * 1000:.1f}ms")

    model_task.cancel()
    tool_task.cancel()
    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())