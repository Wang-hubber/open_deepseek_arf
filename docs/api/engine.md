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

---

## 按功能域的 API 参考

按用户任务路径分 5 组：**启动与配置 → 运行控制 → Checkpoint 扩展点 → 路由策略 → 资源池**。每组下的 API 按调用顺序排列。

### 1. 启动与配置 (Boot & Configure)

#### 1.1 `AgentConfig`

声明式 Engine 配置。

```python
class AgentConfig:
    def __init__(
        agent_id: str = "agent",
        provider: str = "mock",
        model: str = "mock-v1",
        system_prompt_template: str = "You are helpful.",
        max_turns: int = 10,
        routes: dict[str, Route] | None = None,
        checkpoint_rules: list[CheckpointRule] | None = None,
    ) -> None: ...
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `agent_id` | `str` | `"agent"` | Agent 唯一标识。Engine 在 Bus 上的 `node_id` 形如 `engine/{agent_id}`。 |
| `provider` | `str` | `"mock"` | 模型供应商名（`"mock"` / `"minimax"` / `"deepseek"` / `"openai"` / `"anthropic"`）。Engine 路由时按此匹配 `ModelAdapterNode.capabilities["provider"]`。 |
| `model` | `str` | `"mock-v1"` | 模型名。匹配 `ModelAdapterNode.capabilities["models"]` 列表中的某项。 |
| `system_prompt_template` | `str` | `"You are helpful."` | 每轮注入到 `state.messages[0]` 的系统提示词。 |
| `max_turns` | `int` | `10` | 单次 `engine.run()` 的 turn 上限。超过后强制抛异常。 |
| `routes` | `dict[str, Route]` | `None` | 按 msg_type 配置 Route（phase 6.2）。key 例：`"model_call"` / `"tool_exec"`。 |
| `checkpoint_rules` | `list[CheckpointRule]` | `None` | Checkpoint 规则列表（phase 6.5）。 |

**属性（只读）：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `.agent_id` | `str` | Agent 标识 |
| `.max_turns` | `int` | turn 上限 |
| `.routes` | `dict[str, str]` | Route 映射（msg_type → 路由摘要字符串，仅用于 inspect） |
| `.checkpoint_rules` | `list[dict]` | Checkpoint 规则（每项 `{"name", "trigger"}`） |

**注意：** `AgentConfig` 实例**只能用于一次** `EngineBuilder.build()`。第二次 `build()` 会抛 `RuntimeError: AgentConfig already used by another build()`。

**示例：**

```python
# 最简：默认配置
cfg = AgentConfig()

# 命名 + 选模型
cfg = AgentConfig(agent_id="code-reviewer", provider="minimax", model="MiniMax-M3")

# 详细：自定义 system prompt + 限制 turn + Route + Checkpoint
cfg = AgentConfig(
    agent_id="quick-qa",
    provider="openai",
    model="gpt-4o-mini",
    system_prompt_template="You are a precise Q&A bot. Answer in one sentence.",
    max_turns=3,
    routes={"model_call": Route.strict(ids=[NodeId("model/openai")])},
    checkpoint_rules=[
        CheckpointRule(
            name="snapshot",
            trigger=Checkpoint.RoundEnd,
            actions=[ActionMessage(msg_type="snapshot_state", payload={})],
        ),
    ],
)
```

---

#### 1.2 `EngineBuilder`

构建 `Engine` 的工厂。

```python
class EngineBuilder:
    @staticmethod
    def new(buses: list[Bus]) -> EngineBuilder: ...

    async def build(self, config: AgentConfig) -> Engine: ...
```

**`new()` 静态方法：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `buses` | `list[Bus]` | 至少 1 个 Bus。Engine 把它们当作可路由的多 Bus（多 Bus 路由 Phase 6 task 6.4+）。 |

**`new()` 异常：**

| 异常类型 | match 文本 | 触发 |
|---------|-----------|------|
| `ValueError` | `"EngineBuilder requires at least one bus"` | `buses` 列表为空 |

**`build()` 方法：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `config` | `AgentConfig` | 上面定义的配置对象。**消费式**——build 后该 config 不可再用。 |

**`build()` 异常：**

| 异常类型 | match 文本 | 触发 |
|---------|-----------|------|
| `RuntimeError` | `"AgentConfig already used by another build()"` | 同一个 config 调了两次 `build()` |
| `RuntimeError` | `"builder already consumed"` | EngineBuilder 调了两次 `build()` |
| `Exception` | `"no model_call responder: ..."` | Bus 没有 `node_type == "model"` 节点，且 `AgentConfig.routes` 没有 `model_call` 配置 |
| `Exception` | `"no tool_exec responder: ..."` | Bus 没有 `node_type == "mcp"` 节点，且 `AgentConfig.routes` 没有 `tool_exec` 配置 |
| `Exception` | `"Checkpoint 输出的 msg_type 'X' 未在 AgentConfig.routes 注册"` | `CheckpointRule.actions` 里的 `msg_type` 没在 routes 中注册 |
| `Exception` | `"Discovery route Capability 无任何节点匹配: ..."` | `Route.discovery` 的 requirements 在 Bus 当前节点上无匹配 |

**示例：**

```python
# 单 Bus
engine = await EngineBuilder.new(buses=[bus]).build(config=cfg)

# 多 Bus —— Engine 会跨 Bus 路由
engine = await EngineBuilder.new(buses=[bus, bus2]).build(config=cfg)
```

> **注意：** `EngineBuilder.new()` 是 `@staticmethod`，必须用 `EngineBuilder.new(buses=[bus])` 调用。

---

### 2. 运行控制 (Runtime)

#### 2.1 `Engine.run`

跑一轮 ReAct 循环，把 `user_input` 注入到 `state`，返回模型最终回复。

```python
class Engine:
    @property
    def agent_id(self) -> NodeId: ...
    @property
    def system_prompt(self) -> str: ...
    async def run(self, state: EngineState, user_input: str) -> str: ...
```

**属性（只读）：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `.agent_id` | `NodeId` | Engine 在 Bus 上的 node_id，格式 `engine/{agent_id}` |
| `.system_prompt` | `str` | 当前的 system prompt（与 `AgentConfig.system_prompt_template` 同步） |

**`run()` 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `state` | `EngineState` | 对话状态。同一 state 可多次 `run()` 累积多轮。 |
| `user_input` | `str` | 本轮用户输入。自动追加为 `state.messages` 中 `role="user"` 的消息。 |

**返回：** `str` — 模型的最终回复（assistant 的 text content）。某些模型（如 MiniMax-M3）的回复以 `<think>...</think>` 块开头。

**`run()` 异常：** 详见 [异常速查表](#异常速查表)。常见：
- `"超过 max_turns (N)"` — 陷入循环
- `"engine already consumed by a previous run"` — Engine 实例并发调用

**最小示例（实际运行耗时：~5ms with mock）：**

```python
import asyncio
from arf import (
    Bus, NodeId, NodeInfo, MessageFilter,
    AgentConfig, EngineBuilder, EngineState,
)


async def attach_mock(bus):
    mock = await bus.connect(
        info=NodeInfo(
            node_id="model/mock",
            node_type="model",
            capabilities={"provider": "mock", "models": ["mock-v1"]},
        ),
        filter=MessageFilter(types=["model_call"]),
    )

    async def resp():
        while True:
            try:
                msg = await mock.recv()
                cid = msg.payload.get("correlation_id") if isinstance(msg.payload, dict) else None
                await mock.send(
                    msg_type="model_response",
                    to=[msg.sender],
                    payload={
                        "correlation_id": cid,
                        "message": {"role": "assistant", "content": "hi back"},
                        "finish_reason": "stop",
                    },
                )
            except Exception:
                break

    return mock, asyncio.create_task(resp())


async def main():
    bus = Bus()
    mock, task = await attach_mock(bus)
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(agent_id="ex-run"),
    )
    state = EngineState()
    out = await engine.run(state=state, user_input="hi")
    print(out)  # 'hi back'

    task.cancel()
    await bus.shutdown()


asyncio.run(main())
```

**输出：** `hi back` (mock, ~5ms)

**易错点：**
- Engine 实例不是线程安全, 不要在同一个 Engine 上并发 `run()`
- state 在 `run()` 期间被持有, 结束后归还。同一 state 可以多次复用
- 如果模型回复含 `<think>...</think>` 标签, 这些会出现在返回的 `output` 字符串里
- 多轮对话时, 复用同一个 `state` —— Engine 自动累积历史

---

#### 2.2 `EngineState`

对话状态持有者。

```python
class EngineState:
    def __init__(self) -> None: ...
    @property
    def round_count(self) -> int: ...
    @property
    def turn_count(self) -> int: ...
    @property
    def context_tokens(self) -> int: ...
    @property
    def messages(self) -> list[dict]: ...
```

无公共参数构造。

**属性（只读）：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `.round_count` | `int` | 已完成的 `engine.run()` 次数。**跨** `run()` 累积。 |
| `.turn_count` | `int` | 累计的 model_call + tool_exec 步数。每次 `model_call` 或 `tool_exec` +1。 |
| `.context_tokens` | `int` | 当前 `model_response.usage.prompt_tokens` 的精确值（API 报告的 prompt token 数）。如果 provider 不返回 `usage`, 则保持上一次的值（或首轮 0）。 |
| `.messages` | `list[dict]` | 完整对话历史，每条 dict 形如 `{"role", "content", "tool_call_id", "name", "tool_calls": [...]}` |

**`messages` 中每条 dict 的字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `role` | `str` | `"system"` / `"user"` / `"assistant"` / `"tool"` |
| `content` | `str` | 消息文本 |
| `tool_call_id` | `str \| None` | 仅 `role="tool"` 时有值，对应 assistant 发的 tool_call id |
| `name` | `str \| None` | 工具名（仅 tool 消息） |
| `tool_calls` | `list[dict]` | 仅 `role="assistant"` 且有工具调用时非空，每项 `{id, name, arguments, target}` |

**最小示例（多轮 state 累积）：**

```python
import asyncio
from arf import (
    Bus, NodeInfo, MessageFilter,
    AgentConfig, EngineBuilder, EngineState,
)


async def attach(bus, replies):
    mock = await bus.connect(
        info=NodeInfo(node_id="model/mock", node_type="model",
                      capabilities={"provider": "mock", "models": ["mock-v1"]}),
        filter=MessageFilter(types=["model_call"]),
    )

    async def resp():
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
                    payload={"correlation_id": cid,
                             "message": {"role": "assistant", "content": reply["content"]},
                             "finish_reason": "stop"},
                )
            except Exception:
                break

    return mock, asyncio.create_task(resp())


async def main():
    bus = Bus()
    mock, task = await attach(bus, [
        {"content": "Hi!"},
        {"content": "Earlier you said Hi."},
    ])
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(agent_id="ex-multi"),
    )
    state = EngineState()
    out1 = await engine.run(state=state, user_input="Hello")
    out2 = await engine.run(state=state, user_input="What did I just say?")
    print(f"out1={out1}, out2={out2}, messages={len(state.messages)}")

    task.cancel()
    await bus.shutdown()


asyncio.run(main())
```

**输出（mock）：** `out1=Hi!, out2=Earlier you said Hi., messages=5` (~4ms)

**易错点：**
- `state.messages` 的 list / dict 是**只读副本**。修改它不会影响 Engine 内部状态
- `round_count` 在每次 `run()` 完成后 +1；`turn_count` 在每次 `model_call` 或 `tool_exec` 后 +1
- `context_tokens` 来自 API 的 `usage.prompt_tokens`（精确值）。Mock model 不返回 usage 时, 此字段保持上一次的值或 0
- 序列化整个 state 用 `json.dumps({"round_count": ..., "turn_count": ..., "messages": ...})` —— 参考 [状态序列化模式](#状态序列化与持久化)

---

---

### 3. 扩展点: Checkpoint (Extension Points)

Engine 在 5 个固定位置触发 checkpoint（phase 6 task 6.5）。每个 Checkpoint 触发时，Engine 串行发送 `CheckpointRule.actions` 列表里的 `ActionMessage`，应用层订阅这些消息实现快照、限速、人工审批、Park/Resume 等能力。

#### 3.1 `Checkpoint` — 5 个触发位置（class attr 单例）

```python
class Checkpoint:
    BeforeModelCall: Checkpoint  # 每次 model_call 之前
    AfterModelCall: Checkpoint   # 收到 model_response 之后
    BeforeToolExec: Checkpoint   # 发 tool_exec 之前
    AfterToolExec: Checkpoint    # 收到 tool_result 之后
    RoundEnd: Checkpoint         # 一轮 ReAct 结束（最终返回前）
```

| 位置 | 触发时机 |
|------|---------|
| `Checkpoint.BeforeModelCall` | 每次 `model_call` 之前 |
| `Checkpoint.AfterModelCall` | 收到 `model_response` 之后 |
| `Checkpoint.BeforeToolExec` | 发 `tool_exec` 之前 |
| `Checkpoint.AfterToolExec` | 收到 `tool_result` 之后 |
| `Checkpoint.RoundEnd` | 一轮 ReAct 结束（最终返回前） |

**示例：**

```python
from arf import Checkpoint

# 5 个值都是单例
assert Checkpoint.BeforeModelCall is Checkpoint.BeforeModelCall
assert Checkpoint.RoundEnd != Checkpoint.BeforeModelCall
```

---

#### 3.2 `ActionMessage` — 触发时发的消息

`Engine → checkpoint` ActionMessage 的 Python 包装。`CheckpointRule.actions` 接受 `list[ActionMessage]` — 预先构造好触发时要发的消息。

```python
class ActionMessage:
    def __new__(cls, msg_type: str, correlation_id: str | None = None, payload: dict | None = None) -> ActionMessage: ...
    @property
    def msg_type(self) -> str: ...
    @property
    def correlation_id(self) -> str: ...
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `msg_type` | `str` | 必填 | 消息类型字符串（`"model_call"` / `"tool_exec"` / `"snapshot_state"` 等）。**必须**在 `AgentConfig.routes` 中注册 |
| `correlation_id` | `str \| None` | `None`（自动 UUID v4） | 关联 ID |
| `payload` | `dict \| None` | `{"correlation_id": "..."}` | 消息体（JSON 可序列化对象） |

**示例：**

```python
from arf import ActionMessage

msg = ActionMessage(msg_type="snapshot_state", payload={"reason": "context_80pct"})
print(msg.msg_type)         # "snapshot_state"
print(msg.correlation_id)   # 自动生成 UUID
```

---

#### 3.3 `CheckpointRule` — 声明式 Checkpoint 规则

```python
class CheckpointRule:
    def __new__(
        cls,
        name: str,
        trigger: Checkpoint,
        actions: list[ActionMessage],
    ) -> CheckpointRule: ...
    @property
    def name(self) -> str: ...
    @property
    def trigger(self) -> Checkpoint: ...
```

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 规则名（出现在 trace / log 中） |
| `trigger` | `Checkpoint` | 触发位置（5 选 1） |
| `actions` | `list[ActionMessage]` | 触发时按顺序发出的消息列表 |

> **与 Rust 实现的差异：** Rust 端 `CheckpointRule::new` 接受 `Box<dyn Fn>` 闭包用于 `when` 和 `build` —— 跨语言边界不可行。Python 版预构造 `actions` 列表，按顺序发送，避免闭包桥接。Phase 6 task 6.22.4 当前仅取 `actions[0]` 作为单消息触发 — 多 action fan-out 是后续任务。

**示例：每轮结束发快照消息**

```python
from arf import Checkpoint, CheckpointRule, ActionMessage, AgentConfig

cfg = AgentConfig(
    agent_id="snap-demo",
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
)
print(f"rule attached: {cfg.checkpoint_rules[0]['name']}")
```

**输出：** `rule attached: snapshot_round_end`

**易错点：**
- `CheckpointRule` 必须通过 `AgentConfig.checkpoint_rules` 注入, 当前 Engine 不接受运行时 mutation
- `actions` 列表里的消息按顺序发送, 但都是同步 `await` —— 慢 consumer 会拖慢 Engine
- Checkpoint 触发时发的消息 `msg_type` **必须**在 `AgentConfig.routes` 注册（即使你只是要丢弃它）, 否则 build 时校验失败
- 应用层订阅 `msg_type="snapshot_state"` 等自定义消息来响应 checkpoint

---

---

### 4. 路由策略 (Routing)

Engine 默认按 `AgentConfig.provider` / `model` 自动匹配 `ModelAdapterNode`。当需要把 `model_call` 路由到指定节点，或把 `tool_exec` 路由到指定 MCP 节点时，使用 `AgentConfig.routes` 显式注入 `Route`。**所有 checkpoint 触发的消息也必须在 routes 中注册**（详见 [§ 3](#3-扩展点-checkpoint-extension-points)）。

#### 4.1 `Route` — 路由策略

```python
class Route:
    @staticmethod
    def strict(ids: list[NodeId]) -> Route: ...
    @staticmethod
    def discovery(requirements: list[tuple[str, str]]) -> Route: ...
```

| 构造器 | 行为 |
|--------|------|
| `Route.strict(ids=[NodeId("model/a"), ...])` | 定向发给指定 NodeId 列表（点对点） |
| `Route.discovery(requirements=[("kind", "model"), ...])` | 路由给 capabilities 包含全部 (key, value) 对的节点（按 bus.graph() 自动发现） |

**示例：**

```python
from arf import Route, NodeId, AgentConfig

# 严格路由：只发给 model/minimax
r1 = Route.strict(ids=[NodeId("model/minimax")])

# 发现路由：路由给 capabilities.provider == "minimax" 的所有节点
r2 = Route.discovery(requirements=[("provider", "minimax")])

# 注入 AgentConfig
config = AgentConfig(
    agent_id="routed",
    routes={
        "model_call": r2,  # 模型按 capabilities.provider 发现
        "tool_exec": Route.strict(ids=[NodeId("mcp/tools")]),  # 工具严格定向
    },
)
print(f"routes: {list(config.routes.keys())}")
```

**输出：** `routes: ['model_call', 'tool_exec']`

> **匹配规则：** `Route.discovery` 按 capabilities 字段做精确匹配（`==`）。常见 fields: `provider`（如 `"minimax"`）, `node_type`（`"model"` / `"mcp"`）。ModelAdapterNode 默认 capabilities 形如 `{"provider": "minimax", "models": ["MiniMax-M3"]}` — 所以上例 `("provider", "minimax")` 能匹配所有连接到 Bus 的 MiniMax model 节点。

---

#### 4.2 `Capability` — `Route.discovery()` 的参数包装

直接构造不需要 —— `Route.discovery(requirements=[(k, v), ...])` 内部自动包装。

```python
class Capability:
    def __new__(cls, requirements: list[tuple[str, str]]) -> Capability: ...
```

**示例：**

```python
from arf import Capability

# 一般不直接构造 — 通过 Route.discovery
cap = Capability(requirements=[("kind", "model"), ("tier", "premium")])
print(f"requirements: {cap.requirements}")
```

---

#### 4.3 `WaitStrategy` — 多响应等待策略

当 `model_call` 通过 `Strict` 路由发给多个 model 节点（投票 / 集成），Engine 需要决定何时认为"收到了足够多的响应"。`WaitStrategy` 决定这个策略：

```python
class WaitStrategy:
    All: WaitStrategy          # 等所有目标节点响应
    Any: WaitStrategy          # 等任一节点响应
    Count(n: int) -> WaitStrategy  # 等 n 个节点响应
```

| 策略 | 行为 |
|------|------|
| `WaitStrategy.All` | 默认。收齐所有目标节点的响应才继续。 |
| `WaitStrategy.Any` | 收到任一节点的响应就继续。 |
| `WaitStrategy.Count(n)` | 收到 n 个节点的响应就继续。 |

**示例：**

```python
from arf import WaitStrategy

# 三个 model 节点投票：等所有 3 个响应
strategy = WaitStrategy.All

# 任何一个响应就够（快但不准）
strategy = WaitStrategy.Any

# 等 2 个（容忍 1 个超时/失败）
strategy = WaitStrategy.Count(n=2)
```

> **当前限制：** `WaitStrategy` 已绑定到 Python，但 `EngineBuilder.build()` 暂未暴露设置接口 —— 目前 Engine 总是按 `All` 处理。等待 `WaitStrategy` 注入 API 是后续任务。

---

#### 4.4 `ModelCall` — Engine 发的 `model_call` 消息

`ModelCall` 是 Engine 在 Bus 上发的消息类型（`msg_type="model_call"`）。开发自定义 Provider 时需要处理这个类型。

```python
class ModelCall:
    def __new__(cls) -> ModelCall: ...
    @property
    def msg_type(self) -> str: ...        # 总是 "model_call"
    @property
    def correlation_id(self) -> str: ...  # UUID v4 字符串
```

**示例：**

```python
from arf import ModelCall

call = ModelCall()
print(call.msg_type)         # "model_call"
print(call.correlation_id)   # "550e8400-e29b-41d4-a716-446655440000"（每次 new 不同）
```

---

---

### 5. 资源池 (Resource Pool)

Pool 用于限流 / 配额 / 共享 connection（phase 6 task 6.13-6.22）。`Lease` 是 RAII 句柄，当 Python 对象 GC 时自动 release 资源（async Drop via `tokio::spawn`）。

#### 5.1 `PoolConfig` + `Overflow` + `PoolError`

```python
class PoolConfig:
    def __new__(
        cls,
        max_size: int = 16,
        overflow: Overflow | None = None,  # 默认 Overflow.Queue(n=0)
        idle_timeout_secs: float | None = None,
    ) -> PoolConfig: ...

class Overflow:
    @staticmethod
    def Reject() -> Overflow: ...
    @staticmethod
    def Queue(n: int) -> Overflow: ...
    @staticmethod
    def Block(timeout_secs: float) -> Overflow: ...

class PoolError(Exception):
    """Pool acquire / release 错误。"""
    # 变体：Full, Timeout, Closed, Acquire(message)
```

**`Overflow` 策略：**

| 策略 | 行为 |
|------|------|
| `Overflow.Reject()` | 资源满时立即抛 `PoolError.Full` |
| `Overflow.Queue(n)` | 缓冲 n 个等待者；超出抛 `PoolError.Full` |
| `Overflow.Block(timeout_secs)` | 阻塞到拿到或超时；超时抛 `PoolError.Timeout` |

**示例：**

```python
from arf import PoolConfig, Overflow

# 容量 3，溢出排队 4 个
cfg = PoolConfig(max_size=3, overflow=Overflow.Queue(n=4), idle_timeout_secs=None)

# 容量 1，立即拒绝
strict = PoolConfig(max_size=1, overflow=Overflow.Reject())

# 容量 2，阻塞等 5 秒
b = PoolConfig(max_size=2, overflow=Overflow.Block(timeout_secs=5.0))
print(f"created 3 pool configs")
```

**输出：** `created 3 pool configs`

---

#### 5.2 `Lease` — RAII 句柄

```python
class Lease:
    """RAII 句柄。Lease 被 GC 时自动 release 资源 (async Drop via tokio::spawn)。"""
    def kind(&self) -> str: ...  # 方法调用: lease.kind() -> "model_adapter" | "mcp"
    def __repr__(self) -> str: ...
```

**当前限制：** `Lease.resource()` 尚未暴露 —— 只可通过 `lease.kind()` 知道是哪类资源。Phase 6 follow-up 把 `Lease<ModelAdapterResource>` 和 `Lease<McpResource>` 分别包装为 `ModelAdapterLease` / `McpLease` 子类。

---

#### 5.3 `ModelAdapterResource` + `ModelAdapterPool`

池化 LLM 调用（限流 / 配额 / 共享 rate-limited API connection）。

```python
class ModelAdapterResource:
    @staticmethod
    def from_provider(provider: Provider) -> ModelAdapterResource: ...
    @property
    def call_count(self) -> int: ...

class ModelAdapterPool:
    @staticmethod
    def with_resources(
        config: PoolConfig,
        resources: list[ModelAdapterResource],
    ) -> ModelAdapterPool: ...
    async def acquire(self) -> Lease: ...
    async def total_count(self) -> int: ...
    async def idle_count(self) -> int: ...
```

**示例：3 个 provider 池化，串行使用避免死锁**

```python
import asyncio
from arf import (
    ModelAdapterPool, ModelAdapterResource,
    PoolConfig, Overflow,
    MiniMaxProvider, MiniMaxConfig,
)


async def main():
    resources = [
        ModelAdapterResource.from_provider(
            provider=MiniMaxProvider(config=MiniMaxConfig.from_env()),
        )
        for _ in range(3)
    ]
    pool = ModelAdapterPool.with_resources(
        config=PoolConfig(max_size=3, overflow=Overflow.Queue(n=0)),
        resources=resources,
    )
    print(f"total={await pool.total_count()}, idle={await pool.idle_count()}")

    # 串行 acquire, 每次用完 lease = None, 然后 sleep 让 async Drop 落定
    for i in range(5):
        lease = await pool.acquire()
        try:
            print(f"acquired {i}: kind={lease.kind()}, repr={lease!r}")
        finally:
            lease = None  # drop ref, trigger async Drop
            await asyncio.sleep(0.01)  # let Drop settle

    print(f"final idle={await pool.idle_count()}")


if __name__ == "__main__":
    asyncio.run(main())
```

**输出（mock provider, 无网络调用）：**
```
total=3, idle=3
acquired 0: kind=model_adapter, repr=Lease(model_adapter)
acquired 1: kind=model_adapter, repr=Lease(model_adapter)
acquired 2: kind=model_adapter, repr=Lease(model_adapter)
acquired 3: kind=model_adapter, repr=Lease(model_adapter)
acquired 4: kind=model_adapter, repr=Lease(model_adapter)
final idle=3
```

**易错点：**
- `lease.kind` 是**方法**（不是属性），必须 `lease.kind()` 调用 — 返回 `"model_adapter"` 或 `"mcp"` 字符串
- `del lease` 或 `lease = None` 触发 Rust 端的 async Drop，但底层是 `tokio::spawn`，所以**下一个 `acquire()` 前需要 `await asyncio.sleep(0.01-0.05)`**让 Drop 任务落定，否则可能偶尔遇到 `PoolError.Full`
- 高并发场景用 `await pool.acquire()` + try/finally，不要用 `gather(*[acquire() for _ in range(N)])` 当 N > pool.total_count() + queue depth，会死锁
- 当前 `Lease.resource()` 未暴露, 只知道 `kind` 字符串, 详细资源属性访问待 Phase 6 follow-up

---

#### 5.4 `McpResource` + `McpPool`

池化 MCP 工具调用（串行化 / 限流 / 共享 MCP connection）。

```python
class McpResource:
    @staticmethod
    def new(mcp_node: McpNode) -> McpResource: ...
    @property
    def call_count(self) -> int: ...

class McpPool:
    @staticmethod
    def with_resources(
        config: PoolConfig,
        resources: list[McpResource],
    ) -> McpPool: ...
    async def acquire(self) -> Lease: ...
    async def total_count(self) -> int: ...
    async def idle_count(self) -> int: ...
```

**示例：容量 1 强制串行化多个 tool_exec**

```python
import asyncio
from arf import McpPool, McpResource, PoolConfig, Overflow
# McpNode 实例构造参考 docs/api/mcp.md

async def main():
    # mcp_node = McpNode.local("tools", "./tools")  # 参考 docs/api/mcp.md
    # 假设已有 mcp_node
    # resources = [McpResource.new(mcp_node=mcp_node)]
    # pool = McpPool.with_resources(
    #     config=PoolConfig(max_size=1, overflow=Overflow.Queue(n=10)),
    #     resources=resources,
    # )
    # async def call(idx):
    #     lease = await pool.acquire()
    #     try:
    #         return idx
    #     finally:
    #         lease = None
    #         await asyncio.sleep(0.01)
    # results = await asyncio.gather(*[call(i) for i in range(5)])
    # print(f"results: {results}")
    print("see docs/api/mcp.md for McpNode construction")

if __name__ == "__main__":
    asyncio.run(main())
```

> **完整可运行示例** 需要先构造 `McpNode`（参考 [`docs/api/mcp.md`](mcp.md)）。`McpPool` 的 Lease 语义同 `ModelAdapterPool` —— `lease = None` + 50ms sleep 让 Drop 落定。

**易错点：** 同 5.3 —— 串行使用避免死锁，`lease = None` + 50ms sleep 让 Drop 落定。

---

（以下章节将在后续 task 续写：常见模式 / 异常速查表 / Python vs Rust / 参考）