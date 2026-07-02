# Multi-turn Conversation

> 🎯 Diátaxis bucket: **Tutorials** — second walk-through.

## Why

`EngineState` persists between calls to `engine.run()` so conversation history accumulates automatically. After each `run()` call, `state.round_count` increments by 1 (one user-input → final-output) while `state.turn_count` may increment by more (one ReAct step per model_call).

## Code

完整可运行脚本（来自 `examples/python/ex02_multi_round.py`）：

```python
"""Multi-round conversation: state accumulates across engine.run() calls.

Demonstrates:
  - EngineState re-used across multiple engine.run()
  - Conversation history preservation
  - round_count vs turn_count semantics

Run: .venv/bin/python py-arf/python/arf/examples/ex02_multi_round.py
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


async def attach_mock_model(bus, replies):
    """Mock model that replays a list of canned responses in order."""
    mock = await bus.connect(
        info=NodeInfo(
            node_id="model/mock",
            node_type="model",
            capabilities={"provider": "mock", "models": ["mock-v1"]},
        ),
        filter=MessageFilter(types=["model_call"]),
    )

    async def responder():
        idx = 0
        while True:
            try:
                msg = await mock.recv()
                cid = msg.payload.get("correlation_id") if isinstance(msg.payload, dict) else None
                reply = replies[idx] if idx < len(replies) else replies[-1]
                idx += 1
                await mock.send(
                    msg_type="model_response",
                    to=[msg.sender],
                    payload={
                        "correlation_id": cid,
                        "message": {"role": "assistant", "content": reply["content"]},
                        "finish_reason": "stop",
                    },
                )
            except Exception:
                break

    return mock, asyncio.create_task(responder())


async def main():
    t0 = time.perf_counter()
    bus = Bus()
    mock, task = await attach_mock_model(
        bus,
        replies=[
            {"content": "Hi! I'm a helpful assistant."},
            {"content": "You asked who I am; I said: I'm a helpful assistant."},
        ],
    )

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(agent_id="ex02-multi-round"),
    )
    state = EngineState()

    out1 = await engine.run(state=state, user_input="Hello, who are you?")
    print(f"turn 1: {out1!r}, messages count={len(state.messages)}")

    out2 = await engine.run(state=state, user_input="What did you just say?")
    print(f"turn 2: {out2!r}, messages count={len(state.messages)}")
    print(f"round_count={state.round_count}, turn_count={state.turn_count}")
    print(f"elapsed={(time.perf_counter() - t0) * 1000:.1f}ms")

    task.cancel()
    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

## Run

```bash
.venv/bin/python examples/python/ex02_multi_round.py
```

预期 stdout（关键行）：

```text
turn 1: "Hi! I'm a helpful assistant.", messages count=2
turn 2: "You asked who I am; I said: I'm a helpful assistant.", messages count=4
round_count=2, turn_count=2
```

## Next

→ [tools.md](tools.md) — 给 Agent 注册一个 mock MCP 工具节点，让 Engine 自动跑 ReAct Reason→Act→Observe 循环。