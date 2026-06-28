# Phase 4 — ModelAdapter 设计

> 父文档：`docs/v1.x/2026-06-26-arfv1-roadmap.md`
> 依赖：Phase 1 (Bus) + Phase 2 (State) + Phase 3 (AgentConfig) — 已完成
> 状态：📝 设计中

## 定位

ModelAdapter 是 Bus 上的**被动节点**——监听 `model_call` 消息，将 ARF 内部 `ModelMessage` 格式翻译为供应商 API 格式，通过 HTTP 调用模型，将响应转回 `model_response` 发到 Bus。

```
Engine ──model_call──→ Bus ──model_call──→ ModelAdapter
                                              │ HTTP (reqwest)
                                              ▼
                                        DeepSeek / OpenAI / Anthropic
                                              │
Engine ←─model_response── Bus ←─model_response─┘
```

**核心原则：可插拔。** 新增供应商 = 新增一个 `impl Provider`，不影响任何其他组件。多个 ModelAdapter 节点可同时在线（如一个 deepseek 节点 + 一个 openai 节点），Engine 根据 `AgentConfig.models` 优先级选择。

## 依赖关系

```
arf-core: ModelMessage, TaskId, Message, NodeId, NodeInfo
    ↑
arf-bus: Bus, NodeHandle, MessageFilter
    ↑
arf-model-adapter ─── depends on: arf-core + arf-bus + reqwest + tokio + serde_json
```

不依赖 `arf-state`、不依赖 `arf-agent`。

## 数据结构

### ModelParams — Engine → ModelAdapter

从 `ModelSpec` 提取，作为 `model_call` payload 的一部分。

```rust
/// Model inference parameters extracted from ModelSpec.
///
/// These are ARF-standard params. Each Provider translates them to
/// its native API format (see per-provider conversion notes).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelParams {
    /// Sampling temperature (0.0–2.0). None = provider default.
    pub temperature: Option<f32>,
    /// Hard limit on output tokens. None = provider default.
    /// Note: Anthropic requires max_tokens — if None, provider uses a safe default.
    pub max_tokens: Option<u32>,
    /// Whether thinking/reasoning is enabled.
    /// - DeepSeek: maps to `thinking: {type: "enabled"/"disabled"}`
    /// - OpenAI: ignored (no native thinking support)
    /// - Anthropic: maps to extended thinking budget
    pub thinking_enabled: bool,
    /// Provider-specific extra parameters (e.g., top_p, reasoning_effort).
    /// ModelAdapter passes this through to the API as-is where safe,
    /// or transforms it as needed (e.g., DeepSeek reads `reasoning_effort`
    /// from extra and maps it into the `thinking` object).
    pub extra: serde_json::Value,
}
```

### ToolDef — 工具定义（传给模型）

```rust
/// Tool definition sent to the model for function calling.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDef {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value, // JSON Schema
}
```

### ModelCallPayload — model_call 消息 payload

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelCallPayload {
    /// Full conversation history in ARF internal format.
    pub messages: Vec<ModelMessage>,
    /// Available tool definitions for function calling.
    pub tools: Vec<ToolDef>,
    /// Model inference parameters.
    pub model_params: ModelParams,
    /// Whether to stream the response. Default true.
    /// When true, ModelAdapter sends `model_response_chunk` messages
    /// during generation, then a final `model_response` on completion.
    #[serde(default = "default_stream")]
    pub stream: bool,
}

fn default_stream() -> bool { true }
```

### ModelResponseChunk — model_response_chunk 消息 payload

每个 chunk 是独立的 Bus 消息，Engine 收到后实时推送给用户。

```rust
/// A single chunk in a streaming response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelResponseChunk {
    /// Chunk type: "text", "reasoning", "tool_call", "usage", "done".
    pub chunk_type: String,
    /// Text delta (for "text" chunks).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content: Option<String>,
    /// Reasoning delta (DeepSeek thinking mode, for "reasoning" chunks).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reasoning: Option<String>,
    /// Tool call delta (for "tool_call" chunks).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call: Option<ToolCallDelta>,
    /// Final usage stats (sent on "usage" or "done" chunks).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usage: Option<Usage>,
    /// Finish reason (sent on "done" chunk).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub finish_reason: Option<String>,
}

/// Incremental tool call update during streaming.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallDelta {
    pub index: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// JSON fragment — caller accumulates across chunks.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub arguments_delta: Option<String>,
}
```

`chunk_type` 枚举：
- `"text"` — 文本内容 delta，Engine 累积拼接为最终 `content`
- `"reasoning"` — 推理过程 delta（DeepSeek `reasoning_content`），Engine 存入 `message.extra.reasoning_content`
- `"tool_call"` — 工具调用增量，Engine 按 `index` 累积 `arguments_delta` 拼成完整 JSON
- `"usage"` — Token 用量信息（通常出现在流末尾）
- `"done"` — 流结束信号，携带 `finish_reason` 和最终 `usage`

### ModelResponsePayload（流结束汇总）

流结束后发送完整消息，与 chunk 累积结果一致，直接存入 State。

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelResponsePayload {
    pub message: ModelMessage,
    pub tool_calls: Option<Vec<ToolCall>>,
    pub finish_reason: String,
    pub usage: Option<Usage>,
    pub id: String,
    pub model: String,
}
```

### ModelResponsePayload — model_response 消息 payload

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelResponsePayload {
    /// The assistant message to append to State.messages.
    pub message: ModelMessage,
    /// Tool call requests from the model, if any.
    pub tool_calls: Option<Vec<ToolCall>>,
    /// Why the model stopped: "stop", "length", "tool_calls", "content_filter".
    pub finish_reason: String,
    /// Token usage statistics.
    pub usage: Option<Usage>,
    /// API response ID for logging/tracing.
    pub id: String,
    /// Actual model that processed the request (may differ from request).
    pub model: String,
}
```

### ToolCall / Usage

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: u32,
    pub output_tokens: u32,
    pub total_tokens: u32,
}
```

---

## Provider trait

```rust
/// Abstract a model provider's chat completion API.
///
/// Each provider (DeepSeek, OpenAI, Anthropic) implements this trait.
/// The trait is `Send + Sync` so it can be shared across tokio tasks.
pub trait Provider: Send + Sync {
    /// Human-readable provider identifier: "deepseek", "openai", "anthropic".
    fn name(&self) -> &str;

    /// Models this provider supports. Used for Bus node_online capabilities.
    fn supported_models(&self) -> &[String];

    /// Send a non-streaming chat completion request.
    ///
    /// Returns the complete response at once. Used when `stream: false`.
    async fn chat(
        &self,
        model_name: &str,
        messages: Vec<ModelMessage>,
        tools: Vec<ToolDef>,
        params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError>;

    /// Send a streaming chat completion request.
    ///
    /// Yields chunks as they arrive from the provider's SSE stream.
    /// The caller (ModelAdapter node) sends each chunk as a Bus message,
    /// then sends a final `ModelResponsePayload` on completion.
    ///
    /// Default implementation falls back to `chat()`: wraps the result
    /// in a single-chunk stream. Providers SHOULD override for true SSE.
    async fn chat_stream(
        &self,
        model_name: &str,
        messages: Vec<ModelMessage>,
        tools: Vec<ToolDef>,
        params: ModelParams,
    ) -> Result<
        (Vec<ModelResponseChunk>, ModelResponsePayload),
        ProviderError,
    > {
        let response = self
            .chat(model_name, messages, tools, params)
            .await?;
        Ok((vec![], response))
    }
}
```

`chat_stream()` 有默认实现（fallback 到非流式），新 Provider 可渐进支持 SSE。
参数 `model_name` 允许一个 Provider 实例支持多个模型。

---

## Provider Config

### Provider Configs

```rust
/// Configuration for a DeepSeek provider.
pub struct DeepSeekConfig {
    /// API base URL. Default: "https://api.deepseek.com/v1".
    pub base_url: String,
    /// API key.
    pub api_key: String,
    /// Supported models (e.g., ["deepseek-v4-flash", "deepseek-v4-pro"]).
    pub models: Vec<String>,
}

/// Configuration for an OpenAI provider.
pub struct OpenAIConfig {
    pub base_url: String,
    pub api_key: String,
    pub models: Vec<String>,
}

/// Configuration for an Anthropic provider.
pub struct AnthropicConfig {
    /// API base URL. Default: "https://api.anthropic.com/v1".
    pub base_url: String,
    /// API key.
    pub api_key: String,
    /// Supported models (e.g., ["claude-sonnet-4-6", "claude-opus-4-7"]).
    pub models: Vec<String>,
}
```

---

## Bus 消息协议

### model_call

Engine → Bus → ModelAdapter。定向消息。`stream: true`（默认）时 ModelAdapter 开启 SSE 流。

```json
{
  "id": "uuid",
  "msg_type": "model_call",
  "from": "engine/session-1",
  "to": ["model/deepseek-v4-flash"],
  "payload": {
    "messages": [...],
    "tools": [...],
    "model_params": {
      "temperature": 0.7,
      "max_tokens": 4096,
      "thinking_enabled": true,
      "extra": {"reasoning_effort": "high"}
    },
    "stream": true
  },
  "timestamp": 1719500000000
}
```

### model_response_chunk（流式）

ModelAdapter → Bus → Engine。每个 SSE chunk 作为一条独立 Bus 消息广播，Engine 实时消费推送给用户。

```json
// 文本 chunk
{
  "msg_type": "model_response_chunk",
  "from": "model/deepseek-v4-flash",
  "to": ["engine/session-1"],
  "payload": {
    "chunk_type": "text",
    "content": "Hello"
  }
}

// 推理 chunk（DeepSeek thinking）
{
  "msg_type": "model_response_chunk",
  "payload": {
    "chunk_type": "reasoning",
    "reasoning": "Let me analyze the question..."
  }
}

// 工具调用增量 chunk
{
  "msg_type": "model_response_chunk",
  "payload": {
    "chunk_type": "tool_call",
    "tool_call": {
      "index": 0,
      "id": "call_abc",
      "name": "read_file",
      "arguments_delta": "{\"path\": \"/x\"}"
    }
  }
}

// 用量 chunk（通常为最后一个 chunk）
{
  "msg_type": "model_response_chunk",
  "payload": {
    "chunk_type": "usage",
    "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
  }
}
```

### model_response（流结束汇总）

ModelAdapter → Bus → Engine。流结束后发送，携带完整消息供 Engine 存入 State。

```json
{
  "id": "uuid",
  "msg_type": "model_response",
  "from": "model/deepseek-v4-flash",
  "to": ["engine/session-1"],
  "payload": {
    "message": {"role": "assistant", "content": "Hello! How can I help?", "extra": {...}},
    "tool_calls": null,
    "finish_reason": "stop",
    "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    "id": "chatcmpl-xxx",
    "model": "deepseek-v4-flash"
  },
  "timestamp": 1719500001000
}
```

### 流式时序

```
Engine                        Bus                    ModelAdapter
  │                            │                         │
  │── model_call(stream:true)─→│─────────→───────────────│ 开启 SSE 流
  │                            │                         │
  │←─── model_response_chunk───│←────── chunk("Hello") ──│
  │←─── model_response_chunk───│←────── chunk(" world")──│
  │←─── model_response_chunk───│←────── usage({...}) ────│
  │←─── model_response─────────│←────── 完整消息 ────────│ 存 State
```

`stream: false` 时跳过 chunk 阶段，直接返回 `model_response`（非流式模式仍支持）。

### node_online（ModelAdapter 注册）

ModelAdapter 连接 Bus 时自动广播：

```json
{
  "msg_type": "node_online",
  "from": "model/deepseek-v4-flash",
  "to": [],
  "payload": {
    "node_type": "model",
    "node_id": "model/deepseek-v4-flash",
    "capabilities": {
      "provider": "deepseek",
      "models": ["deepseek-v4-flash", "deepseek-v4-pro"]
    },
    "online_since": 1719500000000
  }
}
```

Engine 通过 `bus.graph()` 获取此信息，匹配 `ModelSpec.provider` + `model_name`。

---

## 节点生命周期

```
ModelAdapterNode::new(config, bus)
    │
    ├─ 1. connect to Bus → node_online 广播
    │
    ├─ 2. spawn listen loop:
    │      msg = handle.recv()
    │      if msg.msg_type == "model_call" && msg.is_for(me):
    │          payload = parse(msg.payload)
    │          if payload.stream:
    │              stream = provider.chat_stream(...)
    │              for chunk in stream.chunks:
    │                  handle.send("model_response_chunk", to=msg.from, payload=chunk)
    │              handle.send("model_response", to=msg.from, payload=stream.response)
    │          else:
    │              response = provider.chat(...).await
    │              handle.send("model_response", to=msg.from, payload=response)
    │
    └─ 3. on shutdown: disconnect → node_offline 广播
```

ModelAdapter 不主动发送消息——只响应 `model_call`。

## 消息格式转换

每个 `Provider::chat()` 负责双向转换：ARF 内部格式 ↔ 供应商 API 格式。

### DeepSeek / OpenAI（OpenAI 兼容格式）

两者共用 OpenAI-compatible `/v1/chat/completions` 协议。DeepSeek 额外支持 `thinking` 和 `reasoning_content`。

**请求转换（ARF → API）：**

| ARF `ModelMessage` | API `message` |
|-------------------|---------------|
| `role: "system"` | `{"role": "system", "content": "..."}` |
| `role: "user"` | `{"role": "user", "content": "..."}` |
| `role: "assistant"` + text | `{"role": "assistant", "content": "..."}` |
| `role: "assistant"` + tool_calls | `{"role": "assistant", "content": null, "tool_calls": [...]}` |
| `role: "tool"` + `tool_call_id` | `{"role": "tool", "tool_call_id": "...", "content": "..."}` |

**DeepSeek 特殊处理：**
- `thinking_enabled: true` → `extra_body: {"thinking": {"type": "enabled"}}`；若 `extra.reasoning_effort` 存在，合并到 thinking 对象
- `extra.reasoning_content` → 下次请求中 passthrough，维持 thinking 模式连续性
- API endpoint: `https://api.deepseek.com/chat/completions`

**响应转换（API → ARF）：**

| API field | ARF field |
|-----------|----------|
| `choices[0].message.content` | `ModelResponsePayload.message.content` |
| `choices[0].message.tool_calls` | `ModelResponsePayload.tool_calls` |
| `choices[0].message.reasoning_content` (DeepSeek) | `ModelResponsePayload.message.extra.reasoning_content` |
| `choices[0].finish_reason` | `ModelResponsePayload.finish_reason` (normalized) |
| `usage` | `ModelResponsePayload.usage` |
| `id` | `ModelResponsePayload.id` |
| `model` | `ModelResponsePayload.model` |

### Anthropic（Messages API）

使用 `/v1/messages` 端点，格式与 OpenAI 显著不同。

**请求转换（ARF → API）：**

| ARF | Anthropic API |
|-----|---------------|
| `role: "system"` 的第一条消息 | 顶层 `system` 参数（字符串） |
| `role: "user"` | `{"role": "user", "content": "..."}` |
| `role: "assistant"` + text | `{"role": "assistant", "content": [{"type": "text", "text": "..."}]}` |
| `role: "assistant"` + tool_calls | `{"role": "assistant", "content": [{"type": "tool_use", "id": "...", "name": "...", "input": {...}}]}` |
| `role: "tool"` + result | `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", "content": "..."}]}` |

**关键差异：**
- `system` 是顶层参数，不是 messages 数组的一项。Engine 传入的 `system_prompt` 由 Provider 提取并移至顶层
- `max_tokens` 是**必填**参数；若 `ModelParams.max_tokens` 为 `None`，Provider 使用默认值 4096
- 响应 `content` 是 content block 数组（`[{type: "text", text: "..."}, ...]`），Provider 需提取拼接为纯文本
- `stop_reason` 字段名不同，需映射到 ARF 统一的 `finish_reason`

**停止原因映射（API → ARF）：**

| API | 原始值 | ARF `finish_reason` |
|-----|--------|---------------------|
| OpenAI/DeepSeek | `stop` | `"stop"` |
| OpenAI/DeepSeek | `length` | `"length"` |
| OpenAI/DeepSeek | `tool_calls` | `"tool_calls"` |
| OpenAI/DeepSeek | `content_filter` | `"content_filter"` |
| Anthropic | `end_turn` | `"stop"` |
| Anthropic | `max_tokens` | `"length"` |
| Anthropic | `stop_sequence` | `"stop"` |
| Anthropic | `tool_use` | `"tool_calls"` |
| All | error response | `"error"` |

每个 Provider 封装自己的转换逻辑，外部不可见。

---

## 错误处理

```rust
#[derive(Debug)]
pub enum ProviderError {
    /// HTTP transport error (connection refused, timeout, DNS).
    Transport(String),
    /// API returned a non-retryable error (400, 401, etc.).
    Api { status: u16, message: String },
    /// API returned a retryable error (429, 5xx) and retries exhausted.
    RetryExhausted { attempts: u32, last_error: String },
    /// Response parsing failed (unexpected format change).
    Parse(String),
}
```

ModelAdapter 内置重试（429/5xx），默认 3 次，指数退避。重试耗尽后返回 `ProviderError::RetryExhausted`，通过 `model_response` 的 `finish_reason: "error"` 告知 Engine。

---

## 目录结构

```
crates/arf-model-adapter/
├── Cargo.toml
└── src/
    ├── lib.rs              # pub mod, re-exports
    ├── types.rs            # ModelParams, ToolDef, ModelCallPayload,
    │                         ModelResponsePayload, ToolCall, Usage
    ├── error.rs            # ProviderError
    ├── provider.rs         # Provider trait
    ├── deepseek.rs         # DeepSeekProvider impl Provider
    ├── openai.rs           # OpenAIProvider impl Provider
    ├── anthropic.rs        # AnthropicProvider impl Provider
    ├── node.rs             # ModelAdapterNode: Bus lifecycle + listen loop
    └── convert.rs          # Internal format ↔ provider API format helpers
```

---

## 任务拆解

| # | 任务 | 内容 | 产出 |
|---|------|------|------|
| 4.1 | 脚手架 + 类型定义 ✅ | `Cargo.toml`、`types.rs`、`error.rs`、`lib.rs`，253 tests | `crates/arf-model-adapter/` |
| 4.2 | Provider trait | trait 定义 + `chat()` + `chat_stream()` 默认实现 | `provider.rs` |
| 4.3 | DeepSeek provider | HTTP 调用 + SSE 流 + thinking 处理 + reasoning_content 透传 | `deepseek.rs` |
| 4.4 | OpenAI provider | HTTP 调用 + SSE 流 + 标准 OpenAI 格式转换 | `openai.rs` |
| 4.5 | Anthropic provider | HTTP 调用 + SSE 流 + system 顶层参数 + content blocks + stop_reason 映射 | `anthropic.rs` |
| 4.6 | 消息转换 | ARF ↔ Provider API 格式双向转换函数 + 测试 | `convert.rs` |
| 4.7 | ModelAdapter node | Bus 节点生命周期 + listen loop（流式/非流式分发） | `node.rs` |
| 4.8 | 集成测试 | ModelAdapter node + Bus + mock HTTP server（含 SSE） | `tests/` |
| 4.9 | workspace 注册 ✅ | 根 `Cargo.toml` 添加 `arf-model-adapter` | `Cargo.toml` |

---

## 交付标准

- [ ] `cargo test --workspace` 全部通过
- [ ] `arf-model-adapter` 仅依赖 `arf-core` + `reqwest` + `tokio` + `serde_json`
- [ ] DeepSeek / OpenAI provider 可完成实际的 API 调用并返回正确格式
- [ ] ModelAdapter node 正确收发 Bus 消息（`model_call` → `model_response`）
- [ ] 多个 Provider 可同时在线（一个 DeepSeek 节点 + 一个 OpenAI 节点）
- [ ] Provider trait 可 mock（用于 Engine 单测）
- [ ] HTTP 请求支持重试（429/5xx，指数退避）

---

## 附录：三家 API 对比

> 来源：各供应商官方文档（2026/06）

### 端点

| 供应商 | Chat Completion 端点 |
|--------|---------------------|
| OpenAI | `POST /v1/chat/completions` |
| DeepSeek | `POST /chat/completions`（OpenAI 兼容） |
| Anthropic | `POST /v1/messages` |

### 请求：关键差异

| 特性 | OpenAI | DeepSeek | Anthropic |
|------|--------|----------|-----------|
| `system` 位置 | `messages[0]` (role=system) | `messages[0]` (role=system) | 顶层 `system` 参数 |
| `max_tokens` | 可选 | 可选 | **必填** |
| `thinking` / 推理 | 不支持 | `thinking: {type, effort}` | extended thinking |
| `reasoning_effort` | — | `"high"` \| `"max"` | — |
| `frequency_penalty` | ✅ | ❌ 已弃用 | ❌ |
| `presence_penalty` | ✅ | ❌ 已弃用 | ❌ |
| `logprobs` | ✅ | ✅ | ❌ |
| `response_format` | ✅ | ✅ | ❌ |

### 响应：关键差异

| 特性 | OpenAI | DeepSeek | Anthropic |
|------|--------|----------|-----------|
| 消息格式 | `choices[0].message` | `choices[0].message` | `content: [{type, text}]` (blocks) |
| 停止字段 | `finish_reason` | `finish_reason` | `stop_reason` |
| 推理内容 | — | `reasoning_content` | — |
| Token 用量 | `usage` | `usage` (含 cache 字段) | `usage` (不含 `total_tokens`) |

### 停止原因映射（API → ARF）

| 供应商 | API 字段值 | ARF `finish_reason` |
|--------|-----------|---------------------|
| OpenAI/DeepSeek | `stop` | `stop` |
| OpenAI/DeepSeek | `length` | `length` |
| OpenAI/DeepSeek | `tool_calls` | `tool_calls` |
| OpenAI/DeepSeek | `content_filter` | `content_filter` |
| Anthropic | `end_turn` | `stop` |
| Anthropic | `max_tokens` | `length` |
| Anthropic | `stop_sequence` | `stop` |
| Anthropic | `tool_use` | `tool_calls` |

### DeepSeek 模型名称迁移

| 旧名称 | 新名称 | 弃用日期 |
|--------|--------|---------|
| `deepseek-chat` | `deepseek-v4-flash` | 2026/07/24 |
| `deepseek-reasoner` | `deepseek-v4-pro` | 2026/07/24 |
