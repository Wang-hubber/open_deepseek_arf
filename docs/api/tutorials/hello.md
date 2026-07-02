# 你好，ARF

> 🎯 Diátaxis 桶位：**Tutorials**（入门教程，从零到可运行示例）

## 为什么

ARF 最小的可运行单元：一个 `Bus`、一个 mock 模型节点、一次 `engine.run()` 调用。后续两篇教程会在此基础上加多轮对话状态与工具调用。

## 代码

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

## 运行

```bash
.venv/bin/python examples/python/ex01_minimal_mock.py
```

预期 stdout（关键行）：

```text
output='pong from mock'
state.round_count=1, state.turn_count=1
messages: [('system', 'You are helpful.'), ('user', 'hello'), ('assistant', 'pong from mock')]
elapsed=...ms
```

> 注：Engine 自动注入了一条默认 system prompt（`You are helpful.`），所以消息数是 3 而不是 1。

## 下一节

→ [conversation.md](conversation.md) — 让 `EngineState` 在多次 `engine.run()` 之间复用，累积多轮对话。