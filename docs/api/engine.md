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
| **Checkpoint** | 暂停/恢复 | Engine 在关键位置（BeforeModelCall / AfterModelCall / BeforeToolExec / AfterToolExec / RoundEnd）触发 checkpoint |

### 适用场景

- 构建一个能"思考 + 行动"的 AI Agent（最常见的 ReAct 模式）
- 多轮对话：Engine 把对话历史写在 `State.messages`，下一轮自动续上
- 工具增强：模型可调 `McpNode` 注册的工具（读文件、查数据库、调外部 API）
- 上下文压缩与 checkpoint 持久化（Phase 6 task 6.7+）

### 不适用场景

- **一次性纯 LLM 调用**——直接用 `Provider.chat()`，无需 Engine
- **复杂多 Agent 协作**——需要 `EngineBuilder` 多次组合 + A2A Bus（Phase 6 task 6.13+）

---

## 快速上手

下面是最小的可运行例子——`Bus` 上挂一个 mock model node，Engine 跑一轮 ReAct 返回文本。

```python
import asyncio
import time
from arf import (
    Bus, NodeId, NodeInfo, MessageFilter,
    AgentConfig, EngineBuilder, EngineState,
)


async def main():
    t0 = time.perf_counter()
    bus = Bus()

    # 1. 挂一个 mock model node —— 订阅 model_call，回复固定文本
    mock = await bus.connect(
        info=NodeInfo(
            node_id="model/mock",
            node_type="model",
            capabilities={"provider": "mock", "models": ["mock-v1"]},
        ),
        filter=MessageFilter(types=["model_call"]),
    )

    async def mock_responder():
        while True:
            try:
                msg = await mock.recv()
                cid = msg.payload.get("correlation_id") if isinstance(msg.payload, dict) else None
                await mock.send(
                    msg_type="model_response",
                    to=[msg.sender],
                    payload={
                        "correlation_id": cid,
                        "message": {"role": "assistant", "content": "pong from mock"},
                        "finish_reason": "stop",
                    },
                )
            except Exception:
                break

    responder_task = asyncio.create_task(mock_responder())

    # 2. 构造 AgentConfig（声明式：agent_id、provider、model、system prompt、max_turns）
    config = AgentConfig(
        agent_id="e2e-doc",
        provider="mock",
        model="mock-v1",
    )

    # 3. 构造 Engine —— EngineBuilder.new() 是 staticmethod，build() 是 async
    engine = await EngineBuilder.new(buses=[bus]).build(config=config)

    # 4. 构造空 State，每次 chat 用同一个 State 维持对话历史
    state = EngineState()

    # 5. 跑一轮 ReAct
    output = await engine.run(state=state, user_input="hello")

    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"output={output!r}")
    print(f"state.round_count={state.round_count}, state.turn_count={state.turn_count}")
    print(f"state.messages: {[(m['role'], m['content'][:40]) for m in state.messages]}")
    print(f"elapsed={elapsed_ms:.1f}ms")

    responder_task.cancel()
    await bus.shutdown()


asyncio.run(main())
```

**运行输出：**（总耗时 ~5ms）

```
output='pong from mock'
state.round_count=1, state.turn_count=1
state.messages: [('system', 'You are helpful.'), ('user', 'hello'), ('assistant', 'pong from mock')]
elapsed=4.6ms
```

> **Hint:** 上面的 mock model responder 是个常用模式——不依赖任何外部 API 即可在本地跑通整个 ReAct 循环。生产环境换成 `ModelAdapterNode(MiniMaxProvider(...))` 即可调用真实模型（见 [Common Patterns → 真实 LLM 接入](#真实-llm-接入)）。

### 接入真实 LLM（MiniMax）

把 mock node 换成 `ModelAdapterNode(MiniMaxProvider(cfg))` 即可。需要 `MINIMAX_API_KEY` 环境变量：

```python
import asyncio
import os
import time
from arf import (
    Bus, ModelAdapterNode, MiniMaxConfig, MiniMaxProvider,
    AgentConfig, EngineBuilder, EngineState,
)


async def main():
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        print("[skip] MINIMAX_API_KEY not set")
        return

    t0 = time.perf_counter()
    bus = Bus()

    # 1. 构造真实 provider
    cfg = MiniMaxConfig.default()
    cfg.api_key = api_key
    cfg.timeout_secs = 60

    # 2. 注册为 ModelAdapterNode
    model_node = await ModelAdapterNode.new(
        provider=MiniMaxProvider(config=cfg),
        bus=bus,
        node_id="model/minimax",
    )

    # 3. Engine 跑
    config = AgentConfig(
        agent_id="live-doc",
        provider="minimax",
        model="MiniMax-M3",
    )
    engine = await EngineBuilder.new(buses=[bus]).build(config=config)
    state = EngineState()

    output = await engine.run(state=state, user_input="Respond with the single word: PONG")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"output={output!r}")
    print(f"elapsed={elapsed_ms:.0f}ms")
    await bus.shutdown()


asyncio.run(main())
```

**运行输出：**（需 `MINIMAX_API_KEY`，耗时 ~1-2s 含网络）

```
output='PONG'
elapsed=1247ms
```

> **注意：** Engine 会用 `provider` 字段去匹配 `ModelAdapterNode.capabilities["provider"]`、用 `model` 字段匹配 `capabilities["models"]` 列表。本地 mock 例子用 `provider="mock"` + capabilities `{"provider": "mock", "models": ["mock-v1"]}` 对应。

---

## 核心概念

### ReAct 循环生命周期

一次 `engine.run(state, user_input)` 的完整流程：

```
1. State 注入：system_prompt + user_input 写入 state.messages
   state.messages = [system, user]
   state.round_count += 1

2. 第一轮 model_call：
   bus.send("model_call", strict route to model_node, payload={...state.messages, tools, ...})

3. 等 model_response：
   engine 在 bus 上订阅 model_response，匹配 correlation_id
   解析 message.content + message.tool_calls

4. 如果有 tool_calls（决策：调用工具）：
   for each tool_call:
       bus.send("tool_exec", payload={name, arguments, correlation_id})
       等 tool_result
       写入 state.messages（role="tool", content=result, tool_call_id=...）
   回到第 2 步

5. 如果没有 tool_calls（决策：直接回复）：
   写入 state.messages（role="assistant", content=text）
   返回 output = assistant.text

6. max_turns 截断：
   如果 turn_count > max_turns 仍未返回，engine 强制 break + 返回当前内容
```

**关键状态字段：**

| 字段 | 含义 |
|------|------|
| `state.round_count` | 已完成的 `engine.run()` 次数（跨多轮递增） |
| `state.turn_count` | 累计 model_call + tool_exec 次数（每次 +1） |
| `state.context_tokens` | 当前 `messages` 估算的 token 数（仅 top-level summary，**不是**精确分词计数） |
| `state.messages` | 完整对话历史，`[{role, content, tool_call_id, name, tool_calls: [...]}]` |

### 单次调用 vs 多轮对话

Engine 是**有状态**的——`state` 对象在多次 `engine.run()` 之间保持：

```python
state = EngineState()

# 第一轮：用户问"你好"
out1 = await engine.run(state=state, user_input="你好")
# state.messages = [system, "你好", assistant("你好！")]

# 第二轮：用户继续（state 保留前一轮）
out2 = await engine.run(state=state, user_input="你刚才说了什么？")
# state.messages = [system, "你好", assistant("你好！"), "你刚才说了什么？", assistant("我说：你好！")]
# 关键：模型"看见"了前面的对话
```

### WaitStrategy —— 等待多响应的策略

当 `model_call` 通过 `Strict` 路由发给多个 model 节点（投票 / 集成），Engine 需要决定何时认为"收到了足够多的响应"。`WaitStrategy` 决定这个策略：

| 策略 | 行为 |
|------|------|
| `WaitStrategy.All` | 收齐所有目标节点的响应才继续（默认） |
| `WaitStrategy.Any` | 收到任一节点的响应就继续 |
| `WaitStrategy.Count(n)` | 收到 n 个节点的响应就继续 |

> **注：** `WaitStrategy` 已绑定到 Python，但 `EngineBuilder.build()` 暂未暴露设置接口——目前 Engine 总是按 `All` 处理。等待 `WaitStrategy` 注入 API 是后续任务。

### ModelCall —— 引擎发往模型的 ActionMessage

`ModelCall` 是 Engine 在 Bus 上发的消息类型（`msg_type="model_call"`）。开发自定义 Provider 时需要处理这个类型。

```python
from arf import ModelCall

call = ModelCall()
print(call.msg_type)         # "model_call"
print(call.correlation_id)   # UUID v4 字符串
```

### Checkpoint —— 暂停 / 恢复

Engine 在以下 5 个位置触发 checkpoint（Phase 6 task 6.5）：

| 位置 | 触发时机 |
|------|---------|
| `BeforeModelCall` | 每次 `model_call` 之前 |
| `AfterModelCall` | 收到 `model_response` 之后 |
| `BeforeToolExec` | 发 `tool_exec` 之前 |
| `AfterToolExec` | 收到 `tool_result` 之后 |
| `RoundEnd` | 一轮 ReAct 结束（最终返回前） |

Checkpoint 通常用于：限速、人工审批、持久化快照、A/B 测试、Park/Resume。

`CheckpointRule` 已绑定到 Python（见 [API 参考](#checkpointrule)）。

---

## API 参考

### `AgentConfig`

声明式 Engine 配置。

```python
class AgentConfig:
    def __init__(
        agent_id: str = "agent",
        provider: str = "mock",
        model: str = "mock-v1",
        system_prompt_template: str = "You are helpful.",
        max_turns: int = 10,
    ) -> None: ...
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `agent_id` | `str` | `"agent"` | Agent 唯一标识。Engine 在 Bus 上的 node_id 形如 `engine/{agent_id}`。 |
| `provider` | `str` | `"mock"` | 模型供应商名（`"mock"` / `"minimax"` / `"deepseek"` / `"openai"` / `"anthropic"`）。Engine 路由时按此匹配 `ModelAdapterNode.capabilities["provider"]`。 |
| `model` | `str` | `"mock-v1"` | 模型名。匹配 `ModelAdapterNode.capabilities["models"]` 列表中的某项。 |
| `system_prompt_template` | `str` | `"You are helpful."` | 每轮注入到 `state.messages[0]` 的系统提示词。 |
| `max_turns` | `int` | `10` | 单次 `engine.run()` 的 turn 上限。超过后强制 break。 |

**属性（只读）：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `.agent_id` | `str` | Agent 标识 |
| `.max_turns` | `int` | turn 上限 |

**注意：** `AgentConfig` 实例**只能用于一次** `EngineBuilder.build()`。第二次 `build()` 会抛 `RuntimeError: AgentConfig already used by another build()`（`engine.rs:127-129`）。同一 Config 复用请先 clone（`AgentConfig` 不可 clone——重新构造）。

**示例：**

```python
# 最简：默认配置
cfg = AgentConfig()

# 命名 + 选模型
cfg = AgentConfig(
    agent_id="code-reviewer",
    provider="minimax",
    model="MiniMax-M3",
)

# 详细：自定义 system prompt + 限制 turn
cfg = AgentConfig(
    agent_id="quick-qa",
    provider="openai",
    model="gpt-4o-mini",
    system_prompt_template="You are a precise Q&A bot. Answer in one sentence.",
    max_turns=3,
)
```

---

### `EngineBuilder`

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
| `Exception` | `<BuildError 显示文本>` | Bus 未连接 / 节点配置校验失败 |

**示例：**

```python
# 单 Bus
engine = await EngineBuilder.new(buses=[bus]).build(config=cfg)

# 多 Bus —— Engine 会跨 Bus 路由
engine = await EngineBuilder.new(buses=[bus, bus2]).build(config=cfg)
```

> **注意：** `EngineBuilder.new()` 是 `@staticmethod`，必须用 `EngineBuilder.new(buses=[bus])` 调用，不是 `EngineBuilder(buses=[bus])`。

---

### `Engine`

ReAct 循环驱动器。

```python
class Engine:
    @property
    def agent_id(self) -> NodeId: ...
    @property
    def system_prompt(self) -> str: ...
    async def run(self, state: EngineState, user_input: str) -> str: ...
```

无公共构造函数——通过 `EngineBuilder.build()` 创建。

**属性（只读）：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `.agent_id` | `NodeId` | Engine 在 Bus 上的 node_id，格式 `engine/{agent_id}` |
| `.system_prompt` | `str` | 当前的 system prompt（与 `AgentConfig.system_prompt_template` 同步） |

**`run()` 方法：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `state` | `EngineState` | 对话状态。**消费式**——Engine 在 `run()` 期间持有 state，调用结束后归还。同一 state 可多次 `run()` 累积多轮。 |
| `user_input` | `str` | 本轮用户输入。自动追加为 `state.messages` 中 `role="user"` 的消息。 |

**返回：** `str` — 模型的最终回复（assistant 的 text content）。如果触发了 `max_turns` 截断，返回当前累积的文本。

**`run()` 异常：**

| 异常类型 | match 文本 | 触发 |
|---------|-----------|------|
| `RuntimeError` | `"engine already consumed by a previous run"` | 并发调 `run()`（Engine 不是线程安全） |
| `RuntimeError` | `"state already consumed by a previous run"` | 同一 state 在并发 `run()` 中 |
| `Exception` | `<RunError 显示文本>` | Bus 关闭、模型节点离线、checkpoint 失败等 |
| `asyncio.TimeoutError` | — | 调用方用 `asyncio.wait_for(engine.run(...), timeout=N)` 主动超时 |

**示例：**

```python
# 单轮
output = await engine.run(state=state, user_input="Hello")

# 多轮（state 自动累积）
out1 = await engine.run(state=state, user_input="My name is Alice")
out2 = await engine.run(state=state, user_input="What's my name?")  # 模型看见前文
# out2 应当回答 "Alice"

# 超时保护
import asyncio
try:
    output = await asyncio.wait_for(
        engine.run(state=state, user_input="complex query"),
        timeout=30.0,
    )
except asyncio.TimeoutError:
    print("Engine hung — model not responding")
```

> **重要：** `engine.run()` 是 **async** 但 Engine 实例本身**不**是线程安全的。同一 Engine 上不要并发 `run()`。

---

### `EngineState`

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

**示例：**

```python
state = EngineState()
print(state)  # EngineState(round=0, turn=0, tokens=0)

out = await engine.run(state=state, user_input="hi")
print(state.round_count)  # 1
print(state.turn_count)   # 1（一次 model_call）
print(len(state.messages))  # 3（system + user + assistant）
print(state.messages[0]["role"])   # "system"
print(state.messages[2]["content"])  # out
```

> **注意：** `state.messages` 的 list / dict 是**只读副本**。修改它不会影响 Engine 内部状态。要保存对话历史请序列化整个 state（或在每次 `run()` 后做快照）。

---

### `WaitStrategy`

多响应等待策略。

```python
class WaitStrategy:
    All: WaitStrategy          # 等所有目标节点响应
    Any: WaitStrategy          # 等任一节点响应
    Count(n: int) -> WaitStrategy  # 等 n 个节点响应
```

**单例（类属性）：**

| 属性 | 说明 |
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

---

### `ModelCall`

Engine 发的 `model_call` 消息（`ActionMessage`）。

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
print(call.msg_type)        # "model_call"
print(call.correlation_id)  # "550e8400-e29b-41d4-a716-446655440000"（每次 new 不同）
```

---

### `Checkpoint`

5 个 Checkpoint 触发位置（class attr 单例）。

```python
class Checkpoint:
    BeforeModelCall: Checkpoint
    AfterModelCall: Checkpoint
    BeforeToolExec: Checkpoint
    AfterToolExec: Checkpoint
    RoundEnd: Checkpoint
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

### `ActionMessage`

`Engine → checkpoint` ActionMessage 的 Python 包装。`CheckpointRule.actions` 接受 `list[ActionMessage]`——预先构造好触发时要发的消息。

```python
class ActionMessage:
    def __new__(cls, msg_type: str, payload: dict | None = None) -> ActionMessage: ...
    @property
    def msg_type(self) -> str: ...
    @property
    def correlation_id(self) -> str: ...
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `msg_type` | `str` | 必填 | 消息类型字符串（`"model_call"` / `"tool_exec"` / `"app_checkpoint"` 等） |
| `payload` | `dict \| None` | `None` | 消息体（JSON 可序列化对象） |

**示例：**

```python
from arf import ActionMessage

# 简单文本消息
msg = ActionMessage(msg_type="app_checkpoint", payload={"reason": "context_80pct"})

print(msg.msg_type)        # "app_checkpoint"
print(msg.correlation_id)  # 自动生成 UUID
```

---

### `CheckpointRule`

声明式 checkpoint 规则。绑定到 `AgentConfig.checkpoint_rules`（当前 `AgentConfig` 暂未直接暴露此字段，需通过 Engine 内部 API；Phase 6 follow-up）。

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
| `actions` | `list[ActionMessage]` | 触发时按顺序发出的消息列表（目前 `when` 默认为总是触发） |

> **与 Rust 实现的差异：** Rust 端 `CheckpointRule::new` 接受 `Box<dyn Fn>` 闭包用于 `when` 和 `build`——跨语言边界不可行。Python 版预构造 `actions` 列表，按顺序发送，避免闭包桥接。Phase 6 follow-up 可考虑用 PyO3 closure 桥接。

**示例：**

```python
from arf import Checkpoint, CheckpointRule, ActionMessage

rule = CheckpointRule(
    name="log_every_round",
    trigger=Checkpoint.RoundEnd,
    actions=[
        ActionMessage(msg_type="app_checkpoint", payload={"action": "snapshot_state"}),
    ],
)
print(rule.name)       # "log_every_round"
print(rule.trigger)    # Checkpoint.RoundEnd
```

---

### `Route`

Engine 路由策略。绑定到 `AgentConfig.routes`（详见 [AgentConfig 段](#agentconfig)）。

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
from arf import Route, NodeId

# 严格路由：只发给 model/minimax
r1 = Route.strict(ids=[NodeId("model/minimax")])

# 发现路由：路由给 capabilities 包含 {"kind": "model"} 的所有节点
r2 = Route.discovery(requirements=[("kind", "model")])

# AgentConfig 用法
config = AgentConfig(
    agent_id="routed",
    provider="minimax",
    model="MiniMax-M3",
    routes={
        "model_call": r1,
        "tool_exec": r2,
    },
)
```

> **注入方式：** `AgentConfig(routes={"model_call": Route.strict(...)})` 构造时传入。`routes` 是 `dict[str, Route]`，key 是 `msg_type`，value 是 `Route` 实例。

---

### `Capability`

`Route.discovery()` 的参数包装。直接构造不需要——`Route.discovery(requirements=[(k, v), ...])` 内部自动包装。

```python
class Capability:
    def __new__(cls, requirements: list[tuple[str, str]]) -> Capability: ...
```

**示例：**

```python
from arf import Capability

# 一般不直接构造 — 通过 Route.discovery
cap = Capability(requirements=[("kind", "model"), ("tier", "premium")])
```

---

### `PoolConfig` + `Overflow` + `PoolError` + `Lease`

Pool 通用原语。

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

class Lease:
    """RAII 句柄。`del lease` 自动 release 资源（async Drop）。"""
    @property
    def kind(self) -> str: ...  # "model_adapter" | "mcp"
    def __repr__(self) -> str: ...
```

> **当前限制：** `Lease.resource()` 尚未暴露——只可通过 `Lease.kind` 知道是哪类资源。Phase 6 follow-up 把 `Lease<ModelAdapterResource>` 和 `Lease<McpResource>` 分别包装为 `ModelAdapterLease` / `McpLease` 子类，调用方可直接访问 `.resource().call_count` 等。

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
```

---

### `ModelAdapterResource` + `ModelAdapterPool`

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

**示例：**

```python
import asyncio
from arf import (
    ModelAdapterPool, ModelAdapterResource, PoolConfig, Overflow,
    MiniMaxProvider, MiniMaxConfig,
)

# 3 个 provider 池化
resources = [
    ModelAdapterResource.from_provider(provider=MiniMaxProvider(config=MiniMaxConfig.default()))
    for _ in range(3)
]
config = PoolConfig(max_size=3, overflow=Overflow.Queue(n=6))
pool = ModelAdapterPool.with_resources(config=config, resources=resources)

# 并发 acquire（async 阻塞直到拿到）
async def call(idx: int) -> str:
    lease = await pool.acquire()
    try:
        return lease.kind  # "model_adapter"
    finally:
        del lease  # async Drop → 自动 release

results = await asyncio.gather(*[call(i) for i in range(10)])
print(f"acquired: {len(results)}, total: {await pool.total_count()}")
```

---

### `McpResource` + `McpPool`

池化 MCP 工具调用（串行化 / 限流 / 共享 MCP 连接）。

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

**示例：** 容量 1 强制串行化 5 个并发 tool_exec

```python
import asyncio
from arf import McpPool, McpResource, PoolConfig, Overflow

mcp_node = ...  # 一个 McpNode 实例
resources = [McpResource.new(mcp_node=mcp_node)]
config = PoolConfig(max_size=1, overflow=Overflow.Queue(n=10))
pool = McpPool.with_resources(config=config, resources=resources)

# 5 个并发 — 串行执行（capacity=1）
async def call(idx: int) -> str:
    lease = await pool.acquire()
    try:
        return lease.kind  # "mcp"
    finally:
        del lease

results = await asyncio.gather(*[call(i) for i in range(5)])
assert len(results) == 5
```

> **注意：** `Lease` 的释放是 async Drop——`del lease` 触发后台 `tokio::spawn`，需要在 50ms 内给 release 任务一点时间 settle。如果连续 acquire 偶尔 `PoolError.Full`，加 `await asyncio.sleep(0.05)` 重试。

> **何时需要直接构造 `ModelCall`：** 通常不需要。Engine 内部构造并发送。开发自定义 `Provider` 时可用于解析响应关联。

---

## 常见模式

### Mock Model Node —— 无 API 也能跑 Engine

最常用的本地开发/测试模式。Bus 上挂一个 mock model node，订阅 `model_call` 并回复固定响应：

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, AgentConfig, EngineBuilder, EngineState


async def attach_mock_model(bus, replies):
    """Attach a mock model node to the bus. `replies` is a list of canned responses.

    Each response is a dict with shape:
      {"content": str, "tool_calls": list[dict] | None}

    Returns the mock handle and a task that should be cancelled on shutdown.
    """
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

    task = asyncio.create_task(responder())
    return mock, task


async def main():
    bus = Bus()
    mock, task = await attach_mock_model(
        bus,
        replies=[{"content": "hello from mock"}],
    )

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(agent_id="mock-demo", provider="mock", model="mock-v1"),
    )
    state = EngineState()
    out = await engine.run(state=state, user_input="hi")
    print(f"output={out!r}")
    print(f"state.messages roles: {[m['role'] for m in state.messages]}")

    task.cancel()
    await bus.shutdown()


asyncio.run(main())
```

**运行输出：**（耗时 <10ms）

```
output='hello from mock'
state.messages roles: ['system', 'user', 'assistant']
```

> **Tip:** `attach_mock_model` 是个轻量级 stub——可扩展支持 streaming、tool_call、error injection 等。完整 `crates/arf-e2e/tests/common/provider.rs::ScriptedProvider` 是 Rust 侧对应实现。

### 多轮对话

Engine 持 state——多次 `run()` 自动累积：

```python
import asyncio
from arf import Bus, AgentConfig, EngineBuilder, EngineState
# 假设 attach_mock_model 已 import

async def main():
    bus = Bus()
    mock, task = await attach_mock_model(
        bus,
        replies=[
            {"content": "Hi! I'm a helpful assistant."},
            {"content": "I'm helpful, you said that in turn 1."},
        ],
    )

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(agent_id="multi-round"),
    )
    state = EngineState()

    # 第一轮
    out1 = await engine.run(state=state, user_input="Hello, who are you?")
    print(f"turn 1: {out1!r}, state.messages count={len(state.messages)}")

    # 第二轮 —— state 保留前一轮
    out2 = await engine.run(state=state, user_input="What did you just say?")
    print(f"turn 2: {out2!r}, state.messages count={len(state.messages)}")
    print(f"state.round_count={state.round_count}, state.turn_count={state.turn_count}")

    task.cancel()
    await bus.shutdown()


asyncio.run(main())
```

**运行输出：**（耗时 <10ms）

```
turn 1: "Hi! I'm a helpful assistant.", state.messages count=3
turn 2: "I'm helpful, you said that in turn 1.", state.messages count=5
state.round_count=2, state.turn_count=2
```

### 工具调用（ReAct + MCP）

mock model 返回 `tool_calls`，Engine 自动发 `tool_exec`，收到 `tool_result` 后继续下一轮：

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, AgentConfig, EngineBuilder, EngineState
# 假设 attach_mock_model 已 import

async def main():
    bus = Bus()

    # 1. Mock model —— 第一轮返回 tool_call，第二轮返回最终答案
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

    # 2. Mock MCP node —— 订阅 tool_exec，回 tool_result
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

    # 3. Engine 跑
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            agent_id="weather-bot",
            provider="mock",
            model="mock-v1",
        ),
    )
    state = EngineState()
    out = await engine.run(state=state, user_input="What's the weather in Beijing?")
    print(f"final output: {out!r}")
    print(f"state.messages roles: {[m['role'] for m in state.messages]}")
    print(f"state.turn_count={state.turn_count}")  # 2 model_calls + 1 tool_exec = 3

    model_task.cancel()
    tool_task.cancel()
    await bus.shutdown()


asyncio.run(main())
```

**运行输出：**（耗时 <20ms）

```
final output: 'The weather in Beijing is sunny, 25°C.'
state.messages roles: ['system', 'user', 'assistant', 'tool', 'assistant']
state.turn_count=3
```

> **关键观察：** `state.messages` 现在有 5 条——system / user / assistant(调工具) / tool(工具结果) / assistant(最终回复)。完整 ReAct 工具循环一目了然。

### 真实 LLM 接入（MiniMax）

mock 换 `ModelAdapterNode(MiniMaxProvider(cfg))`：

```python
import asyncio
import os
from arf import Bus, ModelAdapterNode, MiniMaxConfig, MiniMaxProvider, AgentConfig, EngineBuilder, EngineState


async def main():
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        print("[skip] MINIMAX_API_KEY not set")
        return

    bus = Bus()

    # 1. 真实 provider
    cfg = MiniMaxConfig.default()
    cfg.api_key = api_key
    cfg.timeout_secs = 60

    # 2. 注册为 ModelAdapterNode
    await ModelAdapterNode.new(
        provider=MiniMaxProvider(config=cfg),
        bus=bus,
        node_id="model/minimax",
    )

    # 3. Engine —— provider 字段必须和 capabilities["provider"] 匹配
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            agent_id="live-doc",
            provider="minimax",
            model="MiniMax-M3",
        ),
    )
    state = EngineState()
    out = await engine.run(state=state, user_input="What's 2+2? Answer with just the number.")
    print(f"output={out!r}")
    await bus.shutdown()


asyncio.run(main())
```

**运行输出：**（需 `MINIMAX_API_KEY`，耗时 ~1-2s）

```
output='4'
```

### max_turns 截断保护

防御模型陷入无限循环：

```python
import asyncio
from arf import Bus, AgentConfig, EngineBuilder, EngineState
# 假设 attach_mock_model 已 import

async def main():
    bus = Bus()
    # 永远返回 tool_call —— Engine 应该被 max_turns 截断
    infinite_replies = [{"content": "", "tool_calls": [{
        "id": "call_x", "name": "noop", "arguments": {},
    }]}]
    mock, model_task = await attach_mock_model(bus, replies=infinite_replies)

    # tool_exec responder
    mcp = await bus.connect(
        info=NodeInfo(node_id="mcp/noop", node_type="mcp", capabilities={}),
        filter=MessageFilter(types=["tool_exec"]),
    )

    async def tool_resp():
        while True:
            try:
                msg = await mcp.recv()
                cid = msg.payload.get("correlation_id") if isinstance(msg.payload, dict) else None
                await mcp.send(msg_type="tool_result", to=[msg.sender], payload={"correlation_id": cid, "content": "ok", "ok": True})
            except Exception:
                break

    tool_task = asyncio.create_task(tool_resp())

    # max_turns=2 限制 —— 跑 2 步后强制抛异常
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(agent_id="trunc-test", provider="mock", model="mock-v1", max_turns=2),
    )
    state = EngineState()
    try:
        out = await engine.run(state=state, user_input="infinite loop please")
        print(f"unexpectedly got output: {out!r}")
    except Exception as e:
        print(f"engine raised after {state.turn_count} turns: {e}")
        print(f"state.messages count={len(state.messages)}")

    model_task.cancel()
    tool_task.cancel()
    await bus.shutdown()


asyncio.run(main())
```

**运行输出：**（耗时 <10ms）

```
engine raised after 2 turns: 超过 max_turns (2)
state.messages count=4
```

> **解释：** `turn_count=2` —— Engine 跑了 2 步后 `max_turns` 触顶，**抛** `Exception("超过 max_turns (N)")`（不返回空串）。调用方用 `try/except` 捕获，处理"陷入循环"场景。

### 超时保护

Engine 不会自己超时——调用方用 `asyncio.wait_for`：

```python
import asyncio
from arf import Bus, AgentConfig, EngineBuilder, EngineState


async def main():
    bus = Bus()
    # 不挂 model node —— engine 会 hang
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(agent_id="hang-test"),
    )
    state = EngineState()

    try:
        out = await asyncio.wait_for(
            engine.run(state=state, user_input="hi"),
            timeout=1.0,  # 1 秒超时
        )
        print(f"unexpectedly got: {out!r}")
    except asyncio.TimeoutError:
        print("engine hung > 1s — likely no model node on bus")
    finally:
        await bus.shutdown()


asyncio.run(main())
```

**运行输出：**（耗时 ~1s）

```
engine hung > 1s — likely no model node on bus
```

> **推荐：** 生产代码总用 `asyncio.wait_for` 包裹 `engine.run()`，避免模型 API 挂起导致整个 EventLoop 卡住。

### 状态序列化与持久化

把 `EngineState.messages` 写入 JSON，下次启动恢复：

```python
import json
from arf import EngineState

state = EngineState()
# ... 跑 engine.run(state, "hi") ...

# 序列化
snapshot = {
    "round_count": state.round_count,
    "turn_count": state.turn_count,
    "context_tokens": state.context_tokens,
    "messages": state.messages,
}
with open("state.json", "w") as f:
    json.dump(snapshot, f, indent=2, ensure_ascii=False)

# 下次启动：构造一个等价的 state
restored_messages = json.load(open("state.json"))["messages"]
# 手动 replay 一次可恢复上下文，或保存 EngineState JSON
```

---

## 异常速查表

| 异常类型 | match 文本 | 触发原因 |
|---------|-----------|---------|
| `ValueError` | `"EngineBuilder requires at least one bus"` | `EngineBuilder.new(buses=[])` 空列表 |
| `RuntimeError` | `"builder already consumed"` | 同一 `EngineBuilder.build()` 调了两次 |
| `RuntimeError` | `"AgentConfig already used by another build()"` | 同一 `AgentConfig` 传给 `build()` 两次 |
| `RuntimeError` | `"engine already consumed by a previous run"` | 同一 `Engine` 并发 `run()`（Engine 非线程安全） |
| `RuntimeError` | `"state already consumed by a previous run"` | 同一 `EngineState` 在并发 `run()` 中 |
| `Exception` | `"超过 max_turns (N)"` | 累计 `turn_count` 达到 `AgentConfig.max_turns` 上限（陷入循环保护） |
| `Exception` | `<RunError 显示文本>` | 运行时错误：Bus 关闭、模型节点离线、checkpoint 失败等 |
| `asyncio.TimeoutError` | — | `asyncio.wait_for(engine.run(...), timeout=N)` 主动超时 |
| `ValueError` | — | `state.messages` 中的 dict 字段类型错误（开发自定义 Provider 时） |

---

## Python 与 Rust API 差异

| 维度 | Rust (`arf-engine`) | Python (`py-arf`) |
|------|-------------------|-------------------|
| `EngineBuilder::new` | `EngineBuilder::new(buses: Vec<Arc<Bus>>)` 实例方法 | `@staticmethod` `EngineBuilder.new(buses=[bus])` |
| `build()` | `async fn` 返回 `Result<Engine, BuildError>` | `async def` 返回 `Engine` 或抛 `Exception(<BuildError 文本>)` |
| `Engine::run` | `&mut self`（可变借用） | `&self`（内部用 Mutex 保证独占） |
| `EngineState` 字段 | `pub` 直接访问 | 只读 `@property` getter |
| `EngineState.messages` | `Vec<ModelMessage>` | `list[dict]`（PyO3 桥接） |
| `CheckpointRule` 构造 | 接受 `Box<dyn Fn>` 闭包 | 接受 `list[ActionMessage]`（预构造消息列表） |
| `AgentConfig.routes` | 直接字段（`HashMap<String, Route>`） | 构造器 kwarg `routes={"model_call": Route.strict(...)}` |
| `ProviderError` 类型 | 枚举 | Python `Exception`（消息文本是 `Display` 输出） |
| 多 Bus 路由 | `MultiBus::route(msg_type, strategy, recipients)` | `EngineBuilder.new(buses=[bus1, bus2])` 隐式 |

---

## 集成示例：Bus + ModelAdapter + MCP + Engine

把 py-arf 全部 4 个模块串成完整 demo（生产路径）：

```python
import asyncio
import os
from arf import (
    Bus, ModelAdapterNode, MiniMaxConfig, MiniMaxProvider,
    McpNode, RemoteConfig,
    AgentConfig, EngineBuilder, EngineState,
)


async def main():
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        print("[skip] MINIMAX_API_KEY not set")
        return

    bus = Bus()

    # 1. 真实 LLM
    cfg = MiniMaxConfig.default()
    cfg.api_key = api_key
    await ModelAdapterNode.new(
        provider=MiniMaxProvider(config=cfg),
        bus=bus,
        node_id="model/minimax",
    )

    # 2. 远程 MCP（CodeTidy）—— 暴露 base64_encode / hash 等工具
    await McpNode.remote(
        namespace="codetidy",
        config=RemoteConfig(url="https://mcp.codetidy.dev", timeout_secs=30),
    ).connect(bus=bus)
    # McpNode.remote() 是异步构造（HTTP 握手），connect() 后广播 node_online

    # 3. Engine —— 用 model_call 路由到 model/minimax
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            agent_id="full-stack",
            provider="minimax",
            model="MiniMax-M3",
            system_prompt_template="You are a coding assistant. Use available tools when appropriate.",
            max_turns=8,
        ),
    )

    # 4. 跑
    state = EngineState()
    out = await engine.run(
        state=state,
        user_input="Base64-encode the string 'hello' using the codetidy tool.",
    )
    print(f"output={out!r}")
    print(f"turn_count={state.turn_count}, messages={len(state.messages)}")
    await bus.shutdown()


asyncio.run(main())
```

**运行输出：**（需 `MINIMAX_API_KEY` + 网络访问，耗时 ~3-5s 含 MCP 握手 + 模型调用）

```
output='aGVsbG8='
turn_count=3, messages=5
```

> **完整 ReAct 路径：** Engine 发 `model_call`（被 `model/minimax` 接）→ 模型决定调 `base64_encode` 工具 → Engine 发 `tool_exec`（被 `mcp/codetidy` 接）→ 返回 base64 结果 → Engine 发下一轮 `model_call` → 模型拿到结果产出最终文本。

---

## 速查：Engine vs 直接 Provider

| 场景 | 用 `Engine` | 用 `Provider.chat()` |
|------|-------------|---------------------|
| 单次 LLM 调用 | ❌ 杀鸡用牛刀 | ✅ 直接 |
| 多轮对话 | ✅ 状态自动维护 | ❌ 手动管理 messages |
| 工具调用 | ✅ 自动 ReAct 循环 | ❌ 手动解析 tool_calls + 调工具 + 拼 messages |
| Checkpoint / Park-Resume | ✅ | ❌ |
| 测试（无状态） | ❌ 太重 | ✅ 简单 |
| 多模型投票 | ✅（多 Bus 路由） | ❌ |

---

## 参考

- [Bus API](bus.md) — 消息总线基础
- [ModelAdapter API](model-adapter.md) — 4 个 LLM Provider 的 chat / chat_stream
- [MCP API](mcp.md) — 工具注册（本地文件系统 / 远程 HTTP）
- [Phase 6 Engine 设计](../v1.x/phase6/phase6-engine-design.md) — Engine 内部架构与 ActionMessage 模型
- [Phase 6 task 6.10 — py-arf Engine 绑定](../v1.x/phase6/task-6.10-py-arf-engine-bindings.md) — 6.10 任务记录
- [examples/domain_controller](../../examples/domain_controller/src/main.rs) — 跨 Bus facade 的 Rust 参考实现
