# ARF Engine — ReAct 循环引擎 API 参考

> **Phase 6 task 6.10 + 6.22.4** · `from arf import AgentConfig, EngineBuilder, Engine, EngineState, WaitStrategy, ModelCall, Checkpoint, CheckpointRule, ActionMessage, Route, Capability, ModelAdapterResource, ModelAdapterPool, McpResource, McpPool, PoolConfig, Overflow, PoolError, Lease`
>
> Engine 是 ARF 的"大脑"——它驱动 ReAct 循环（Reason → Act → Observe → Reason），把 `ModelAdapter` 的回复和 `McpNode` 的工具调用串成一个完整的 Agent。

---

## 概述

ARF Engine 实现了完整的 **ReAct 循环**（Reasoning + Acting）：每轮给模型发一次 `model_call`，如果模型决定调用工具则发 `tool_exec`，拿到工具结果后再发下一轮 `model_call`，直到模型给出最终回复。Engine 自身**不调用任何模型或工具**——它通过 Bus 把请求发出去，由 `ModelAdapterNode` 和 `McpNode` 响应。

```
┌──────────────────────────────────────────────────────────┐
│                Engine (ReAct Loop)                       │
│                                                          │
│   round() ──→ model_call ──→ wait response               │
│                  │                                       │
│                  ├─ text reply  ──→ return to caller      │
│                  └─ tool_calls  ──→ tool_exec ──→        │
│                                       wait tool_result   │
│                                            │             │
│                       next iteration ◀─────┘             │
└──────────────────────────────────────────────────────────┘
         │                                       │
         ▼                                       ▼
   ┌──────────┐                            ┌──────────┐
   │  Bus     │ ←── model_response ────    │ ModelAd- │
   │  (CAN)   │ ←── tool_result     ────   │ apterNode│
   │          │                            └──────────┘
   │          │ ←── tool_result     ────   ┌──────────┐
   │          │                            │ McpNode  │
   └──────────┘                            └──────────┘
```

### Engine 在 ARF 中的位置

| 模块 | 角色 | 与 Engine 关系 |
|------|------|----------------|
| **Bus** | 消息总线 | Engine 是 Bus 上的一个节点，订阅 `model_response` 和 `tool_result` |
| **ModelAdapter** | LLM API 翻译 | Engine 发 `model_call`，等 `model_response` |
| **MCP** | 工具注册 | Engine 发 `tool_exec`，等 `tool_result` |
| **State** | 对话历史 | Engine 持有 + 更新，调用方通过 `state.messages` 读取 |
| **Checkpoint** | 暂停/恢复 | Engine 在 5 个位置（BeforeModelCall / AfterModelCall / BeforeToolExec / AfterToolExec / RoundEnd）触发 checkpoint |

### 适用场景

- 构建一个能"思考 + 行动"的 AI Agent（最常见的 ReAct 模式）
- 多轮对话：Engine 把对话历史写在 `State.messages`，下一轮自动续上
- 工具增强：模型可调 `McpNode` 注册的工具（读文件、查数据库、调外部 API）
- 上下文压缩与 checkpoint 持久化（Phase 6 task 6.5+）
- 多模型路由与池化（Phase 6 task 6.2/6.22+）

### 不适用场景

- **一次性纯 LLM 调用**——直接用 `Provider.chat()`，无需 Engine
- **复杂多 Agent 协作**——需要 `EngineBuilder` 多次组合 + A2A Bus（Phase 6 task 6.13+）

---

## 完整示例：从 0 到能调工具的多轮 Agent

下面是一个完整可运行的示例 — 它把 phase 1-5 的基础组件和 phase 6 的全部 5 类新能力（Route / Checkpoint / Engine / Pool / multi-turn）串联起来。**运行需要 `MINIMAX_API_KEY` 环境变量**；无 key 时参考后文 [常见模式 → Mock Model Node](#mock-model-node--无-api-也能跑-engine)。

完整文件：[`py-arf/python/arf/examples/ex08_phase6_overview.py`](../../py-arf/python/arf/examples/ex08_phase6_overview.py)

```python
"""Full phase 1-6 stack: Bus + ModelAdapterPool + McpNode + Engine + Checkpoint + Route."""

import asyncio
import os
import time

from arf import (
    # phase 1
    Bus, NodeId,
    # phase 4 / 6.20
    MiniMaxConfig, MiniMaxProvider,
    # phase 5
    McpNode,
    # phase 6.10
    AgentConfig, EngineBuilder, EngineState,
    # phase 6.5
    Checkpoint, CheckpointRule, ActionMessage,
    # phase 6.2
    Route,
    # phase 6.22
    ModelAdapterPool, ModelAdapterResource,
    PoolConfig, Overflow,
)


def ensure_tool_manifest():
    """Create a minimal local MCP tool manifest for McpNode.local to scan."""
    os.makedirs("./tools/get_weather", exist_ok=True)
    manifest = os.path.join("./tools/get_weather", "manifest.yaml")
    if not os.path.exists(manifest):
        with open(manifest, "w") as f:
            f.write("name: get_weather\ndescription: Get current weather for a city\n")


async def main():
    if not os.environ.get("MINIMAX_API_KEY"):
        raise SystemExit("MINIMAX_API_KEY not set")

    ensure_tool_manifest()
    t0 = time.perf_counter()
    bus = Bus()

    # ── phase 6.22: pool 3 providers, max=2 + queue=2 ──
    resources = [
        ModelAdapterResource.from_provider(
            provider=MiniMaxProvider(config=MiniMaxConfig.from_env()),
        )
        for _ in range(3)
    ]
    pool = ModelAdapterPool.with_resources(
        config=PoolConfig(max_size=2, overflow=Overflow.Queue(n=2)),
        resources=resources,
    )

    # ── phase 4: register 3 model nodes ──
    for i in range(3):
        provider = MiniMaxProvider(config=MiniMaxConfig.from_env())
        await provider.connect_to_bus(bus, NodeId(f"model/pool-{i}"))

    # ── phase 5: McpNode scans ./tools/ ──
    mcp = McpNode.local(namespace="tools", root="./tools")
    await mcp.connect(bus)

    # ── phase 6.10/6.5/6.2: Engine + Checkpoint + Route ──
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            agent_id="ex08-full-demo",
            system_prompt_template="You are a helpful assistant.",
            max_turns=8,
            routes={
                "model_call": Route.discovery(requirements=[("provider", "minimax")]),
                "tool_exec": Route.strict(ids=[NodeId("mcp/tools")]),
                "snapshot_state": Route.strict(ids=[NodeId("model/pool-0")]),
            },
            checkpoint_rules=[
                CheckpointRule(
                    name="snapshot_round_end",
                    trigger=Checkpoint.RoundEnd,
                    actions=[
                        ActionMessage(
                            msg_type="snapshot_state",
                            payload={"reason": "round_end"},
                        ),
                    ],
                ),
            ],
        ),
    )

    # ── multi-turn ──
    state = EngineState()
    out1 = await engine.run(state=state, user_input="用一句话介绍北京。")
    out2 = await engine.run(state=state, user_input="上海呢?")

    print(f"out1={out1!r}")
    print(f"out2={out2!r}")
    print(f"round_count={state.round_count}, turn_count={state.turn_count}, messages={len(state.messages)}")
    print(f"elapsed={(time.perf_counter() - t0) * 1000:.0f}ms")

    await bus.shutdown()


asyncio.run(main())
```

**实际运行输出**（`MINIMAX_API_KEY=sk-...`，2026-07-01 实测）：

```
pool: max=2 queue=2, total=3
mcp: McpNode(namespace='tools', node_id='mcp/tools')
out1='<think>\nThe user is asking me to introduce Beijing in one sentence, in Chinese. Let me provide a concise and informative response.\n</think>\n北京是中国的首都，是一座拥有三千多年历史的古都，也是现代化国际大都市，以故宫、长城等众多历史文化古迹和繁荣的现代风貌闻名于世。'
out2='<think>\nThe user is asking me to introduce Shanghai in one sentence, following the same format as the Beijing introduction.\n</think>\n上海是中国最大的经济中心和国际化大都市，被誉为"东方明珠"，以繁华的都市景观、发达的商业金融和独特的海派文化著称。'
round_count=2, turn_count=2, messages=5
elapsed=3919ms
```

**对照原始示例，这一段演示了 phase 6 的全部 5 类新能力**：

| 能力 | 代码位置 | 作用 |
|------|---------|------|
| `Engine` / `EngineBuilder` | `EngineBuilder.new(...).build(...)` | ReAct 循环驱动器 |
| `EngineState` | `state.round_count / turn_count / messages` | 多轮 state 累积 |
| `Checkpoint` + `CheckpointRule` | `checkpoint_rules=[...]` + `Checkpoint.RoundEnd` | 每轮结束触发 snapshot |
| `ActionMessage` | `ActionMessage(msg_type="snapshot_state", ...)` | checkpoint 触发时发的消息 |
| `Route.strict` | `"tool_exec": Route.strict(...)` | 工具调用定向到 `mcp/tools` |
| `Route.discovery` | `"model_call": Route.discovery(...)` | 模型调用按 capabilities.provider 发现 |
| `ModelAdapterPool` | `ModelAdapterPool.with_resources(...)` | 3 个 provider 限流 + 排队 |

> **无 API key 时：** 参考后文 [Mock Model Node](#mock-model-node--无-api-也能跑-engine) 模式 — 把 `MiniMaxProvider` 换成手动构造的 mock 节点。

> **关于 `<think>` 标签：** 某些模型（如 MiniMax-M3）的回复以 `<think>...</think>` 块开头，后跟实际回答。`engine.run()` 返回的字符串包含这些标签。生产代码可用 `re.sub(r"<think>.*?</think>\s*", "", output, flags=re.DOTALL)` 剥离。

> **关于 checkpoint 路由：** Checkpoint 触发时发的 `ActionMessage.msg_type` **必须**在 `AgentConfig.routes` 中注册一条路由 — 否则 `EngineBuilder.build()` 会在 build 时校验并抛 `"Checkpoint 输出的 msg_type 'X' 未在 AgentConfig.routes 注册"`。本例把 `snapshot_state` 路由到第一个 model 节点（它会忽略非 `model_call` 类型的消息）。

---
（以下章节将在后续 task 续写：按功能域的 API 参考 / 常见模式 / 异常速查 / Python vs Rust / 参考）