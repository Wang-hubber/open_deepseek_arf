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
        print(event.data.content)  # 完整文本
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
| `model_call` | `async (stream=True) → ModelResult \| ModelStream` | 发起 LLM 调用，默认流式，详见下文 |
| `wait` | `(hook_name, reason) → WaitItem` | 向 `state.waiting[hook_name]` 追加等待项，同步方法不阻塞 |
| `finish_wait` | `(wait_id, reason="") → dict` | 移除等待项，返回更新后的 `state.waiting` |
| `stop` | `() → AgentState` | 停用 agent 并返回完整状态用于持久化 |
| `resume` | `(state, call_model, stream_model=None) → PrimitiveAgent` | 从持久化状态重建 agent（类方法） |

---

## `model_call()` 详细文档

### 签名

```python
async def model_call(self, stream: bool = True) -> ModelResult | ModelStream
```

### 行为

读取 `state.messages` 全部消息，构造 `[{role, content}]` 列表，传给 `call_model` 或 `stream_model` 发起 API 调用。

```
state.messages ──► [{"role": ..., "content": ...}] ──► ModelAdapter ──► LLM API
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
    usage: dict = {}                      # {prompt_tokens, completion_tokens, total_tokens}
    finish_reason: str = "stop"           # "stop" | "tool_calls"
```

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
        result = event.data
        print(f"本轮完成: {result['content'][:50]}...")
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
    └─ yield AgentEvent("model_call_end", result)    # 完成信号
```

App 消费 stream 和 Harness 回写 state **完全解耦，互不依赖**。流式路径下 `model_call_end` 仍会 emit（`collect_response`、测试等需要它作为完成信号）。

### 兼容性

- 当 `_stream_model` 为 None 时（旧构造、无模型配置），默认流式会 fallback 到非流式
- `_noop` 占位（测试场景）提供空 generator
