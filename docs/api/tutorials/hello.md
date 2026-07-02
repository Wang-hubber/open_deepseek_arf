# Hello, ARF

> 🎯 Diátaxis bucket: **Tutorials** — first-time walk-through.

## Why

The smallest runnable unit in ARF: one `Bus`, one mock model node, and one `engine.run()` call. Subsequent tutorials build on this by adding multi-turn state and tool calls.

## Code

完整可运行脚本（来自 `examples/python/ex01_minimal_mock.py`）：

```python
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
```

## Run

```bash
.venv/bin/python examples/python/ex01_minimal_mock.py
```

预期 stdout（关键行）：

```text
output='pong from mock'
state.round_count=1, state.turn_count=1
messages: [('assistant', 'pong from mock')]
elapsed=...ms
```

## Next

→ [conversation.md](conversation.md) — 让 `EngineState` 在多次 `engine.run()` 之间复用，累积多轮对话。