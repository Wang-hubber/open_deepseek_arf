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
| `.context_tokens` | `int` | 当前 messages 的估算 token 数（粗略字符估算，**不是**精确分词）。 |
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
- `context_tokens` 是粗略估算（基于字符数），不是精确分词
- 序列化整个 state 用 `json.dumps({"round_count": ..., "turn_count": ..., "messages": ...})` —— 参考 [状态序列化模式](#状态序列化与持久化)

---

（以下章节将在后续 task 续写：3. Checkpoint / 4. Route / 5. Pool）