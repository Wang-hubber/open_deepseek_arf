# Agent

## 概念

Agent = `name` + `system_prompt` + `models`。它是一个**被动的消息状态机**，由外部 Harness 驱动执行。

```
┌─ AgentConfig (agent.yaml) ──┐     ┌─ AgentHarness ───────────────┐
│ name / system_prompt        │     │ run() — ReAct 主循环         │
│ models / model_defs         │ ──► │ checkpoints — 插件调度       │
└─────────────────────────────┘     │ park / resume — 人机等待     │
        │                           └──────────────┬───────────────┘
        ▼                                          │
┌─ PrimitiveAgent ────────────┐                    │
│ state: messages, waiting    │ ◄──────────────────┘
│ input() / model_call()      │    Harness 读写 state
│ wait() / finish_wait()      │
│ stop() / resume()           │
└─────────────────────────────┘
```

**核心原则**：PrimitiveAgent 只知道消息和模型调用，不知道 tools/hooks/sandbox/events。这些是 Harness + Plugin 的职责。Agent 提供 mechanism，Harness 决定 how。

---

## 组装 (注册)

两条路径，二选一：

### 路径 A：`AgentHarnessFactory.create_harness()`（推荐）

```python
from arf.harness.factory import create_harness

harness = await create_harness("agent.yaml")

async for event in harness.run("用户输入"):
    if event.type == "model_chunk":
        send_sse(event.data)       # 流式输出
    elif event.type == "model_call_end":
        print(event.data["content"])  # 完整文本
        print(event.data["usage"])    # token 用量
```

### 路径 B：`BaseAgent`（兼容封装）

```python
from arf.agent.base import BaseAgent
from arf.agent.config import AgentConfig

config = AgentConfig.from_yaml("agent.yaml")
agent = BaseAgent(config)

async for event in agent.astream("用户输入"):
    ...
```

内部实际是 `PrimitiveAgent` + `AgentHarness`，`BaseAgent` 仅做薄封装。

### agent.yaml 最小示例

```yaml
schema_version: "1.0"
name: my-agent

model_defs:
  - model: deepseek-chat
    api_base: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    context_window: 131072
    temperature: 0.7
    kwargs:
      thinking_enabled: true
      reasoning_effort: high

system_prompt:
  prefix:
    role: "你是一个有用的助手"
    critical_rules: "禁止编造文件路径"
```

---

## PrimitiveAgent

### 构造函数

```python
PrimitiveAgent(
    agent_id: str,
    model_config: dict,                     # {api_base, api_key_env, model_name, context_window}
    call_model: Callable[[list[dict], list[dict] | None], Awaitable[ModelResult]],
    stream_model: Callable[[list[dict], list[dict] | None], AsyncIterator[dict]] | None = None,
)
```

| 参数 | 说明 |
|------|------|
| `agent_id` | 唯一标识，通常取自 `AgentConfig.name` |
| `model_config` | 模型元信息，持久化到 `AgentState`，resume 时用 |
| `call_model` | 非流式调用函数，由 `_build_call_model()` 注入（`ModelDegrader.chat_complete`） |
| `stream_model` | 流式调用函数，由 `_build_call_model()` 注入（`ModelDegrader.chat_stream_full`），可为 None |

### 状态属性 `state: AgentState`

```python
@dataclass
class AgentState:
    agent_id: str
    session_id: str                        # Harness 在 session 开始时赋值
    messages: list[Message]                # [{message_id, role, content}]
    waiting: dict[str, list[WaitItem]]     # hook_name → [WaitItem]
    model_config: dict                     # 构造时传入
```

### 6 个原语

| 方法 | 签名 | 说明 |
|------|------|------|
| `input` | `(role, content, position="end") → Message` | 向 `state.messages` 注入一条消息，role 可以是 `"system"` `"user"` `"assistant"` `"tool"` |
| `model_call` | `async (stream=True, tools=None) → ModelResult \| ModelStream` | 发起 LLM 调用，默认流式，详见下文 |
| `wait` | `(hook_name, reason) → WaitItem` | 向 `state.waiting[hook_name]` 追加等待项，同步方法不阻塞 |
| `finish_wait` | `(wait_id, reason="") → dict` | 移除等待项，返回更新后的 `state.waiting` |
| `stop` | `() → AgentState` | 停用 agent 并返回完整状态用于持久化 |
| `resume` | `(state, call_model, stream_model=None) → PrimitiveAgent` | 从持久化状态重建 agent（类方法） |

---

## `model_call()` 详细文档

### 签名

```python
async def model_call(self, stream: bool = True, tools: list[dict] | None = None) -> ModelResult | ModelStream
```

### 行为

读取 `state.messages` 全部消息，构造 `[{role, content}]` 列表，传给 `call_model` 或 `stream_model` 发起 API 调用。

| 参数 | 类型 | 说明 |
|------|------|------|
| `stream` | `bool` | `True` 返回 `ModelStream`（流式），`False` 返回 `ModelResult` |
| `tools` | `list[dict] \| None` | OpenAI 格式工具定义列表。传参后 LLM 可获得工具调用能力。`None` 表示纯文本对话。 |

```
state.messages ──► [{"role": ..., "content": ...}] ──► ModelAdapter ──► LLM API
       tools ────────────────────────────────────────────┘
```

**非流式** (`stream=False`)：
```
result: ModelResult = await agent.model_call()
# result.content      → 完整文本
# result.tool_calls   → [{id, name, params}]
# result.usage        → {prompt_tokens, completion_tokens, total_tokens}
# result.finish_reason → "stop" | "tool_calls"
```

**流式** (`stream=True`，默认)：
```
stream: ModelStream = await agent.model_call(stream=True)
async for chunk in stream:
    # chunk → raw dict，直接转发给 App
result = stream.result   # 迭代结束后可用，聚合好的 ModelResult
```

### 返回值类型

#### `ModelResult`（非流式）

```python
@dataclass
class ModelResult:
    content: str                          # 完整文本
    tool_calls: list[dict] = []           # [{id, name, params}]
    usage: dict = {}                      # {prompt_tokens, completion_tokens, total_tokens}，模型不返回时为空 dict
    finish_reason: str = "stop"           # "stop" | "tool_calls"
```

> **注意**：部分模型不在响应中返回 usage。此时 `usage` 为空 dict `{}`，不会缺 key 或报错。消费方通过 `data.get("usage", {}).get("total_tokens", 0)` 安全取值。

#### `ModelStream`（流式）

既是 `AsyncIterator[dict]`，又提供 `.result` 聚合属性：

```python
class ModelStream:
    def __aiter__(self) → self
    async def __anext__(self) → dict     # 迭代 raw chunk
    @property
    def result(self) → ModelResult       # 迭代结束后可用
```

**Chunk 类型**（来自 `ModelAdapter.chat_stream_full`）：

| chunk["type"] | 说明 | 示例 |
|------|------|------|
| `chunk` | 文本增量，可能含 `reasoning` | `{"type":"chunk","content":"Hello","reasoning":"用户说Hello..."}` |
| `tool_call_chunk` | 工具调用增量 | `{"type":"tool_call_chunk","name":"read","arguments":"{\"pa","id":"call_0","delta":"{\"pa"}` |
| `tool_call` | 完整工具调用（`finish_reason=tool_calls` 时） | `{"type":"tool_call","name":"read","arguments":"{\"path\":\"/a\"}","id":"call_0"}` |
| `usage` | token 用量 | `{"type":"usage","prompt_tokens":100,"completion_tokens":50,"total_tokens":150}` |
| `error` | API 错误 | `{"type":"error","code":429,"detail":"..."}` |

**`.result` 聚合逻辑**（`ModelStream._finalize`）：

```
"chunk" chunks      → content_parts.join("")     → ModelResult.content
"tool_call" chunks  → tool_calls dict            → ModelResult.tool_calls
"usage" chunk       → usage dict                 → ModelResult.usage
has tool_calls      → finish_reason="tool_calls" else "stop"
```

> **注意**：`tool_call_chunk` 是增量更新（App 可用于流式展示进度），`tool_call` 是完整结果。聚合时框架只用 `tool_call` 构建 `ModelResult`。

### App 消费模式

```python
async for event in harness.run("用户输入"):
    if event.type == "model_chunk":
        # 流式：逐 chunk 实时展示
        chunk = event.data
        if chunk["type"] == "chunk":
            if "reasoning" in chunk:
                show_reasoning(chunk["reasoning"])
            show_content(chunk["content"])
        elif chunk["type"] == "tool_call_chunk":
            show_tool_progress(chunk["name"], chunk["delta"])
        elif chunk["type"] == "tool_call":
            show_tool_call(chunk["name"], chunk["arguments"])

    elif event.type == "model_call_end":
        # 聚合完成，state 已回写
        data = event.data
        print(f"本轮完成: {data['content'][:50]}..., tokens={data.get('usage', {})}")
```

### Harness 内部流程

```
agent.model_call(stream=True) → ModelStream
    │
    ├─ async for chunk in stream:
    │     yield AgentEvent("model_chunk", chunk)     # App 消费，实时 SSE
    │
    ├─ result = stream.result                        # 聚合好的 ModelResult
    │
    ├─ agent.input("assistant", result)              # 回写 state.messages
    │     │
    │     └─ 如有 tool_calls:
    │          content = {"content": str, "tool_calls": list}
    │        否则:
    │          content = str
    │
    └─ yield AgentEvent("model_call_end", {content, tool_calls, usage, finish_reason})
```

App 消费 stream 和 Harness 回写 state **完全解耦，互不依赖**。流式路径下 `model_call_end` 仍会 emit（`collect_response`、测试等需要它作为完成信号）。

### 兼容性

- 当 `_stream_model` 为 None 时（旧构造、无模型配置），默认流式会 fallback 到非流式
- `_noop` 占位（测试场景）提供空 generator

---

## API 参考

### 配置加载

#### `AgentConfig.from_yaml(path)`

从 `agent.yaml` 文件加载 Agent 配置。

```python
from arf.agent.config import AgentConfig

config: AgentConfig = AgentConfig.from_yaml("agent.yaml")
# config.name          → "my-agent"
# config.model_defs    → [{model, api_base, api_key_env, ...}]
# config.system_prompt → SystemPromptConfig
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `path` | `str \| Path` | agent.yaml 文件路径 |

| 返回 | 类型 | 说明 |
|------|------|------|
| config | `AgentConfig` | 验证后的 Pydantic 配置模型 |

---

### 工厂入口

#### `create_harness(agent_config_path, ...)`

从 YAML 配置一站式创建 `AgentHarness`。内部完成：配置加载、ModelAdapter 构建、PrimitiveAgent 创建、Plugin 发现与实例化、`McpClientManager` 组装（含 kernel 工具注册 + SkillIndex 初始化 + `await tool_manager.start()`）。

```python
from arf.harness.factory import create_harness

harness = await create_harness(
    agent_config_path="agent.yaml",
    harness_config_path="harness.yaml",   # 可选，默认在 agent.yaml 同级
    plugin_dir="path/to/plugins",         # 可选，默认 arf/plugins/
    event_bus=None,                       # 可选，默认 InMemoryEventBus
    data_dir="./data",                    # 可选，默认 ./data
)

async for event in harness.run("hello"):
    ...
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `agent_config_path` | `str` | (必需) | agent.yaml 路径 |
| `harness_config_path` | `str \| None` | `None` | harness.yaml 路径，默认在 agent.yaml 同级查找 |
| `plugin_dir` | `str \| None` | `None` | 插件目录，默认 `arf/plugins/` |
| `event_bus` | `Any` | `None` | 事件总线，默认创建 `InMemoryEventBus` |
| `data_dir` | `str` | `"./data"` | 数据根目录（traces/state/memory 放在此下） |

| 返回 | 类型 | 说明 |
|------|------|------|
| harness | `AgentHarness` | 组装完成的执行器，调用 `run()` 启动 |

---

### Agent 生命周期

#### `agent.start()`

空操作，占位用于未来资源初始化（MCP 连接、文件监听等）。

```python
await agent.start()
```

#### `agent.stop()`

清理活跃会话，释放资源。不会删除持久化的 session state。

```python
await agent.stop()
```

---

### 对话执行

#### `agent.astream(user_message, session_id)`

流式执行一个对话轮次，逐 `AgentEvent` yield。

```python
async for event in agent.astream("你好", session_id="my-session"):
    if event.type == "model_chunk":
        chunk = event.data
        if chunk["type"] == "chunk":
            ui_stream(chunk["content"])
        elif chunk["type"] == "reasoning" in chunk:
            ui_reasoning(chunk["reasoning"])

    elif event.type == "model_call_end":
        data = event.data
        # data = {content, tool_calls, usage, finish_reason}

    elif event.type == "error":
        handle_error(event.data["detail"])
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `user_message` | `str` | (必需) | 用户输入文本 |
| `session_id` | `str` | `"default"` | 会话标识。空串或空白 → 自动生成 UUID |
| `stop_on_text` | `bool` | `False` | 保留参数，当前未使用 |

| 返回 | 类型 | 说明 |
|------|------|------|
| events | `AsyncIterator[AgentEvent]` | 流式 AgentEvent 序列 |

**Session 切换行为**：每次调用 `astream()` 会先 reset agent 内部状态（`messages`、`waiting`），然后检查 `session_id` 是否有存档——有则恢复 messages，无则新鲜启动。旧会话的 messages 不会泄漏到新会话。

#### `agent.run(user_message, session_id)`

便捷方法：内部调用 `astream()`，收集所有事件后返回最终文本。适用于不需要流式展示的场景（如标题生成）。

```python
title = await agent.run("为对话生成标题", session_id="title-gen")
# title → "关于天气的讨论"
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `user_message` | `str` | (必需) | 用户输入文本 |
| `session_id` | `str` | `"default"` | 会话标识 |

| 返回 | 类型 | 说明 |
|------|------|------|
| text | `str` | 最终模型输出文本（通过 `collect_response` 收集） |

---

### 会话状态管理

通过 `agent.state_store` 访问，实现为 `FileStateStore`（文件持久化）或 `InMemoryStateStore`（测试用）。

#### `state_store.put(session_id, state)`

写入会话状态。

```python
await agent.state_store.put("my-session", {
    "session_id": "my-session",
    "messages": [{"role": "user", "content": "你好"}, ...],
    "session_active": True,
})
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `session_id` | `str` | 会话标识 |
| `state` | `dict` | 任意可 JSON 序列化的 dict |

`FileStateStore` 写入路径为 `{data_dir}/{session_id}/state/{session_id}.json`。

#### `state_store.get(session_id)`

读取会话状态。不存在时返回 `None`。

```python
state = await agent.state_store.get("my-session")
# state → {"session_id": "my-session", "messages": [...], ...}
# 或 None
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `session_id` | `str` | 会话标识 |

| 返回 | 类型 | 说明 |
|------|------|------|
| state | `dict \| None` | 会话状态，无存档时为 `None` |

#### `state_store.delete(session_id)`

删除会话存档。

```python
await agent.state_store.delete("my-session")
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `session_id` | `str` | 要删除的会话标识 |

#### `state_store.list_sessions()`

列出所有有存档的会话 ID。

```python
sessions = await agent.state_store.list_sessions()
# sessions → ["abc123", "def456", ...]
```

| 返回 | 类型 | 说明 |
|------|------|------|
| sessions | `list[str]` | 会话 ID 列表（`FileStateStore` 返回排序后的列表） |

---

### 工具过滤

`AgentConfig` 控制每个 Agent 哪些工具可用：

```yaml
plugins: [filesystem]       # 启用的插件（插件工具挂到 {plugin}__ namespace）
tools: [read_file, grep]    # 启用的 user__ 工具（空/缺省 = 全部）
```

过滤规则：
- `kernel__` — 始终可用（`ask_user`、`use_skill`、`task_complete`）
- `user__` — 按 `tools` 列表过滤，空列表 = 全部
- `{plugin}__` — 按 `plugins` 列表过滤
- `{server}__` — 远程 MCP 工具，配置了 `mcp_servers` 就全部可用

---

### 工具执行

工具执行收口到 `McpClientManager`——单一入口，namespace 路由：

```
AgentHarness.run()
  └─ tool_manager.execute_batch(tool_calls)    # asyncio.gather 并行
       └─ tool_manager.execute(name, params)   # 单次调用，按 namespace 路由
            ├─ kernel__     → 进程内 handler (use_skill, ask_user, task_complete)
            ├─ user__       → ToolProvider — 本地 tools/ 目录 function.py
            ├─ {plugin}__   → PluginProvider — 插件自带 function.py
            └─ {server}__   → 远程 MCP subprocess (local_server.py)
```

**并行优先**：`AgentHarness` 优先调用 `execute_batch()`（`asyncio.gather` 并行执行全部 tool call）。仅当 `tool_manager` 不提供 `execute_batch` 时才 fallback 顺序执行。

**容错**：`get_tool_definitions()` 失败时打日志继续无工具运行，不会崩溃整个 run loop。

#### `McpClientManager.execute_batch(tool_calls)`

```python
async def execute_batch(self, tool_calls: list[dict]) -> dict[str, ToolResult]
```

并行执行多个工具调用。每个 tool call 独立——一个失败不影响其他。

| 参数 | 类型 | 说明 |
|------|------|------|
| `tool_calls` | `list[dict]` | `[{id, name, params}]` — 模型返回的工具调用列表 |

| 返回 | 类型 | 说明 |
|------|------|------|
| results | `dict[str, ToolResult]` | `{call_id: ToolResult}` — 每个调用的结果 |

#### `McpClientManager.execute(name, params)`

```python
async def execute(self, tool_name: str, params: dict) -> ToolResult
```

执行单个工具。按 namespace 前缀路由到对应 provider。

| 参数 | 类型 | 说明 |
|------|------|------|
| `tool_name` | `str` | 带 namespace 前缀的工具名（如 `user__read_file`） |
| `params` | `dict` | 工具参数 |

| 返回 | 类型 | 说明 |
|------|------|------|
| result | `ToolResult` | `success`、`data`、`error`、`blocked` |

---

### AgentEvent

Harness 执行循环产出的事件流。App 通过 `async for event in harness.run()` 或 `agent.astream()` 消费。

```python
from arf.core.events import AgentEvent

@dataclass
class AgentEvent:
    type: str          # 事件类型（见下方）
    data: dict         # 事件负载
    timestamp: float   # 事件时间戳
    trace_id: str      # trace 标识
    session_id: str    # 当前会话 ID
    agent_name: str    # agent 名称
    turn: int          # 当前 turn 编号
    primitive: str     # "input" | "action" | "output" | "wait"
    level: str         # "session" | "round" | "turn"
```

#### 常用事件类型

| `event.type` | `event.data` 内容 | 触发时机 |
|------|------|------|
| `model_chunk` | `{type, content, reasoning?, ...}` | 流式模型输出的每个 chunk |
| `model_call_end` | `{content, tool_calls, usage, finish_reason}` | 模型调用完成（聚合结果） |
| `tool_call_start` | `{name, id}` | 工具执行开始 |
| `tool_call_end` | `{name, id, success}` | 工具执行完成 |
| `error` | `{detail}` | 发生错误 |
| `parked` | `{hook_name, waiting}` | 执行暂停，等待人工输入 |
| `approval_required` | `{decision_id, ...}` | 需要人工审批 |
| `task_completed` | — | 任务完成 |

#### 获取 session_id

`astream()` 传入的 `session_id` 可能为空（自动生成 UUID），从事件中获取实际使用的 ID：

```python
async for event in agent.astream("hello"):
    sid = event.session_id  # 框架实际使用的 session_id
    ...
```

---

### 兼容工具

位于 `arf.engine.compat`，用于简化事件流消费。

#### `collect_response(astream)`

遍历事件流，收集最终文本。等价于 `agent.run()` 的内部实现。

```python
from arf.engine.compat import collect_response

text = await collect_response(agent.astream("hello", session_id="s1"))
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `astream` | `AsyncGenerator[AgentEvent, None]` | 事件流 |

| 返回 | 类型 | 说明 |
|------|------|------|
| text | `str` | 最后一个 `model_call_end` 事件的 `content` |

#### `collect_events(astream)`

收集所有事件到列表，用于测试断言。

```python
from arf.engine.compat import collect_events

events = await collect_events(agent.astream("hello"))
assert any(e.type == "model_call_end" for e in events)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `astream` | `AsyncGenerator[AgentEvent, None]` | 事件流 |

| 返回 | 类型 | 说明 |
|------|------|------|
| events | `list[AgentEvent]` | 所有事件 |

#### `drain_astream(engine, state)`

消费引擎的事件流，返回最终持久化状态。用于旧式 `engine.invoke()` 的替代。

```python
from arf.engine.compat import drain_astream

final_state = await drain_astream(engine, initial_state)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `engine` | — | 引擎实例（有 `astream()` 方法） |
| `state` | `dict` | 初始状态 dict |

| 返回 | 类型 | 说明 |
|------|------|------|
| state | `dict` | `state_store.get()` 返回的最终持久化状态 |
