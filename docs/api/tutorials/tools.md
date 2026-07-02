# Tool Use

> 🎯 Diátaxis bucket: **Tutorials** — third walk-through.

## Why

A second mock node is added — this time an MCP tool node subscribed to `tool_exec` messages. When the model returns `tool_calls`, the Engine automatically routes a `tool_exec` to the MCP node, waits for the `tool_result`, and feeds it back into the model — a full Reason → Act → Observe loop with no engine-internal glue code required.

## Code

完整可运行脚本（来自 `examples/python/ex03_tool_call.py`）：

```python
"""ReAct tool call: Engine routes model_response.tool_calls to mock MCP.

Demonstrates:
  - Mock model returns tool_calls, Engine auto-dispatches tool_exec
  - Mock MCP node subscribes to tool_exec, replies with tool_result
  - Engine performs full Reason -> Act -> Observe -> Reason loop

Run: .venv/bin/python py-arf/python/arf/examples/ex03_tool_call.py
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
                tool_calls = reply.get("tool_calls")
                message = {"role": "assistant", "content": reply.get("content", "")}
                await mock.send(
                    msg_type="model_response",
                    to=[msg.sender],
                    payload={
                        "correlation_id": cid,
                        "message": message,
                        "tool_calls": tool_calls,
                        "finish_reason": "tool_calls" if tool_calls else "stop",
                    },
                )
            except Exception:
                break

    return mock, asyncio.create_task(responder())


async def main():
    t0 = time.perf_counter()
    bus = Bus()

    # Mock model: turn 1 = tool_call, turn 2 = final answer
    mock, model_task = await attach_mock_model(
        bus,
        replies=[
            {
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "name": "get_weather",
                    "arguments": {"city": "Beijing"},
                }],
            },
            {"content": "The weather in Beijing is sunny, 25°C."},
        ],
    )

    # Mock MCP node: subscribes to tool_exec
    mcp = await bus.connect(
        info=NodeInfo(
            node_id="mcp/weather",
            node_type="mcp",
            capabilities={"tools": [{"name": "get_weather"}]},
        ),
        filter=MessageFilter(types=["tool_exec"]),
    )

    async def tool_responder():
        while True:
            try:
                msg = await mcp.recv()
                cid = msg.payload.get("correlation_id") if isinstance(msg.payload, dict) else None
                tool_name = msg.payload.get("name") if isinstance(msg.payload, dict) else None
                await mcp.send(
                    msg_type="tool_result",
                    to=[msg.sender],
                    payload={
                        "correlation_id": cid,
                        "name": tool_name,
                        "content": "sunny, 25°C",
                        "ok": True,
                    },
                )
            except Exception:
                break

    tool_task = asyncio.create_task(tool_responder())

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            agent_id="ex03-tool-bot",
            provider="mock",
            model="mock-v1",
        ),
    )
    state = EngineState()
    out = await engine.run(state=state, user_input="What's the weather in Beijing?")
    print(f"output: {out!r}")
    print(f"messages: {[m['role'] for m in state.messages]}")
    print(f"turn_count={state.turn_count}")
    print(f"elapsed={(time.perf_counter() - t0) * 1000:.1f}ms")

    model_task.cancel()
    tool_task.cancel()
    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

## Run

```bash
.venv/bin/python examples/python/ex03_tool_call.py
```

预期 stdout（关键行）：

```text
output: 'The weather in Beijing is sunny, 25°C.'
messages: ['system', 'user', 'assistant', 'tool', 'assistant']
turn_count=3
```

> 注：ReAct 循环跨 3 个 turn — turn 1 模型产出 `tool_call`，turn 2 MCP 返回 `tool_result`，turn 3 模型给出最终回答。Engine 还在初始注入一条 system message。

## Next

本系列后续可扩展：state 持久化（`ex06_state_serialize`）、max_turns 与超时（`ex04`/`ex05`）、Phase 6 全栈（`ex08`）。详见 [`examples/python/`](../../examples/python/) 目录。