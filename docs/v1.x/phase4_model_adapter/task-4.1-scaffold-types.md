# 任务 4.1：ModelAdapter 脚手架 + 类型定义

> Phase 4 — ModelAdapter 第一项任务
> 父文档：`docs/v1.x/phase4_model_adapter/phase4-model-adapter-design.md`
> 依赖：Phase 1 (Bus) + Phase 2 (State) + Phase 3 (AgentConfig) — 已完成

## 设计思路

搭建 `arf-model-adapter` crate 骨架，定义所有共享类型。不写 Provider 实现——Provider trait 在 4.2，实现在 4.3–4.5。

| 文件 | 内容 |
|------|------|
| `Cargo.toml` | 依赖 `arf-core` + `arf-bus` + `reqwest` + `tokio` + `serde` + `serde_json` |
| `types.rs` | `ModelParams`, `ToolDef`, `ModelCallPayload`, `ModelResponsePayload`, `ToolCall`, `Usage` |
| `error.rs` | `ProviderError` enum |
| `lib.rs` | 模块声明 + 重新导出 |

所有类型从 design spec 机械转录，`#[derive(Debug, Clone, Serialize, Deserialize)]`。

---

## 代码实现

### `crates/arf-model-adapter/Cargo.toml`

```toml
[package]
name = "arf-model-adapter"
version.workspace = true
edition.workspace = true
license.workspace = true
repository.workspace = true
description = "ARF ModelAdapter: Bus node that translates ARF messages to provider APIs"

[dependencies]
arf-core = { path = "../arf-core" }
arf-bus = { path = "../arf-bus" }
reqwest = { version = "0.12", default-features = false, features = ["json", "rustls-tls"] }
tokio = { version = "1", features = ["sync", "rt", "macros"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

逐行：
- `arf-core` — 使用 `ModelMessage` 作为消息格式，不重复定义对话消息类型
- `arf-bus` — ModelAdapter node 需要 `Bus::connect()` 获取 `NodeHandle`，收发 `model_call` / `model_response`
- `reqwest` — HTTP 客户端调用供应商 API。`default-features = false` + `rustls-tls` 替代默认的 `native-tls`（OpenSSL），消除系统编译依赖。`json` feature 启用 `reqwest::Client::json()` 自动序列化/反序列化
- `tokio` — 异步运行时。`sync`（broadcast channel）、`rt`（`#[tokio::test]`）、`macros`（`tokio::spawn`）
- `serde` + `serde_json` — 类型序列化（Bus 消息的 payload 字段是 `serde_json::Value`）和 HTTP 请求/响应 JSON 编解码

---

### `crates/arf-model-adapter/src/types.rs`

```rust
//! Shared types for ModelAdapter — payloads, params, tool defs, responses.

use arf_core::ModelMessage;
use serde::{Deserialize, Serialize};
```

逐行：
- `use arf_core::ModelMessage` — 从 Phase 2 定义的共享类型导入，不重复造轮子。`ModelMessage` 已有 `role`、`content`、`tool_call_id`、`name`、`extra` 字段，覆盖全部 ARF 对话消息需求
- `use serde` — 所有 payload struct 都需要在 Bus 消息中序列化/反序列化

---

#### ModelParams

```rust
/// Model inference parameters extracted from ModelSpec.
///
/// These are ARF-standard params. Each Provider translates them to
/// its native API format.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelParams {
    /// Sampling temperature (0.0–2.0). None = provider default.
    pub temperature: Option<f32>,
    /// Hard limit on output tokens. None = provider default.
    /// Note: Anthropic requires max_tokens — if None, provider uses a safe default.
    pub max_tokens: Option<u32>,
    /// Whether thinking/reasoning is enabled.
    pub thinking_enabled: bool,
    /// Provider-specific extra parameters (e.g., top_p, reasoning_effort).
    pub extra: serde_json::Value,
}
```

逐行：
- `temperature: Option<f32>` — `None` 表示"不指定，由供应商使用默认值"。`Option` 而非裸 `f32`，因为不同模型有不同的合理默认温度，ARF 不强加。序列化时 `None` 不输出到 JSON
- `max_tokens: Option<u32>` — 同上。特殊：Anthropic API 要求 `max_tokens` 必填，Provider 实现需在 `None` 时自行填入安全默认值（如 4096）
- `thinking_enabled: bool` — ARF 标准字段，不是 `Option` 因为每个 Agent 配置必须明确是否开启思考。各 Provider 自行映射：DeepSeek → `thinking: {type: "enabled"/"disabled"}`；OpenAI → 忽略；Anthropic → extended thinking budget
- `extra: serde_json::Value` — 供应商专属参数黑洞。Engine 从 `ModelSpec.extra` 透传到此，Provider 自行解析。例如 DeepSeek 从中读取 `reasoning_effort` 拼入 thinking 对象。`Value::Null` 表示无额外参数

---

#### ToolDef

```rust
/// Tool definition sent to the model for function calling.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDef {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,
}
```

逐行：
- `name: String` — 工具名。Engine 从 `ToolSpec.name` 提取
- `description: String` — 工具用途的自然语言描述。如果 `ToolSpec.description` 为 `Some`，用它覆盖 MCP 注册的默认描述；`None` 则用 MCP 默认值
- `parameters: serde_json::Value` — JSON Schema 描述工具参数。Provider 直接透传给 API 的 `tools[].function.parameters` 字段
- 三个字段等价于 OpenAI function calling 的 `{type: "function", function: {name, description, parameters}}`，Provider 在 `chat()` 中按需拼接

---

#### ModelCallPayload

```rust
/// Payload of a `model_call` Bus message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelCallPayload {
    pub messages: Vec<ModelMessage>,
    pub tools: Vec<ToolDef>,
    pub model_params: ModelParams,
}
```

逐行：
- `messages: Vec<ModelMessage>` — 完整对话历史。Engine 从 `State.messages` 中取出，包含 system/user/assistant/tool 角色。最后一个消息通常是用户的输入
- `tools: Vec<ToolDef>` — 当前可用的工具定义列表。Engine 从 `AgentConfig.tools` 和 MCP 注册信息组装。为空表示本轮不需要 function calling
- `model_params: ModelParams` — 本次模型调用参数。Engine 从 `AgentConfig.models[selected].*` 提取
- 这是 `model_call` 的 payload 类型——Engine 构造此 struct，序列化为 `Message.payload`，ModelAdapter 解包后调用 `Provider::chat()`

---

#### ModelResponsePayload

```rust
/// Payload of a `model_response` Bus message.
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

逐行：
- `message: ModelMessage` — assistant 角色消息，Engine 直接追加到 `State.messages`。`content` 为模型文本输出，`extra` 存供应商特有数据（如 `reasoning_content`）
- `tool_calls: Option<Vec<ToolCall>>` — 模型请求的工具调用列表。`None` = 纯文本回复（`finish_reason = "stop"`）；`Some` = 模型要求执行工具（`finish_reason = "tool_calls"`）。`Some([])` 不应出现——有 tool_calls 至少有一项
- `finish_reason: String` — 模型停止原因，已标准化为 ARF 统一值。Provider 负责将供应商原生值（如 Anthropic `end_turn`）映射为 `"stop"` / `"length"` / `"tool_calls"` / `"content_filter"` / `"error"`
- `usage: Option<Usage>` — Token 用量统计。流式响应的最后一个 chunk 或供应商不返回时可为 `None`
- `id: String` — API 响应 ID（如 `"chatcmpl-xxx"`），用于日志关联和问题排查。Engine 不解析此值，原样存入 trace
- `model: String` — 实际处理请求的模型名。可能与请求的 `model_name` 不同（路由/fallback 场景）

---

#### ToolCall

```rust
/// A tool call request from the model.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: serde_json::Value,
}
```

逐行：
- `id: String` — 工具调用 ID，模型生成（如 `"call_abc123"`）。Engine 用它关联 `tool` 角色的结果消息
- `name: String` — 工具名，对应 `ToolSpec.name`
- `arguments: serde_json::Value` — 工具参数 JSON。Engine 直接传给 MCP 节点执行，不做格式校验（校验由 MCP 节点负责）

---

#### Usage

```rust
/// Token usage statistics.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: u32,
    pub output_tokens: u32,
    pub total_tokens: u32,
}
```

逐行：
- `input_tokens: u32` — 输入 token 数（prompt tokens）
- `output_tokens: u32` — 输出 token 数（completion tokens）
- `total_tokens: u32` — 总计。OpenAI/DeepSeek 直接提供此字段，Anthropic 不返回 `total_tokens`（需 Provider 自行计算 `input + output`）
- 三个字段足够 ARF 做成本核算和上下文窗口管理。供应商特有的额外 usage 字段（如 DeepSeek 的 `prompt_cache_hit_tokens`）放在 `ModelMessage.extra` 中

---

### `crates/arf-model-adapter/src/error.rs`

```rust
//! Error types for ModelAdapter providers.

/// Errors that can occur when calling a model provider's API.
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

逐行：
- `Transport(String)` — 网络层错误：连接拒绝、DNS 解析失败、TLS 握手超时、连接池耗尽。发生在 HTTP 请求发出之前。包含原始错误信息
- `Api { status, message }` — 非可重试的 API 错误：400 Bad Request（参数错误）、401 Unauthorized（API key 无效）、402 Payment Required、403 Forbidden、404 Not Found。`status` 存 HTTP 状态码，`message` 存响应体中的错误描述
- `RetryExhausted { attempts, last_error }` — 可重试错误（429 Rate Limit、500/502/503/504 Server Error）经过所有重试后仍未成功。`attempts` 记录共尝试了几次（含首次），`last_error` 记录最后一次失败的原因。Engine 可据此决定是否切换模型
- `Parse(String)` — API 返回了 200 但响应体格式不符合预期（JSON 结构变更、缺失必填字段）。保护上层不受供应商 API 变更影响——至少返回可理解的错误而非 panic

```rust
impl std::fmt::Display for ProviderError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Transport(msg) => write!(f, "transport error: {msg}"),
            Self::Api { status, message } => write!(f, "API error {status}: {message}"),
            Self::RetryExhausted { attempts, last_error } => {
                write!(f, "retry exhausted after {attempts} attempts: {last_error}")
            }
            Self::Parse(msg) => write!(f, "parse error: {msg}"),
        }
    }
}

impl std::error::Error for ProviderError {}
```

逐行：
- `impl Display` — 人类可读的错误描述。每种变体输出不同格式：`Transport` 前缀 "transport error:"、`Api` 包含状态码、`RetryExhausted` 显示尝试次数
- `impl std::error::Error` — 满足 Rust 标准错误 trait。允许被 `anyhow`/`eyre` 等 error crate 消费，允许 `Box<dyn Error>` 类型擦除

---

### `crates/arf-model-adapter/src/lib.rs`

```rust
//! ARF ModelAdapter — translate ARF messages to provider APIs.
//!
//! ModelAdapter is a passive Bus node. It listens for `model_call` messages,
//! translates ARF-internal `ModelMessage` format to provider-specific API
//! format, calls the model via HTTP, and returns `model_response` to the Bus.
//!
//! Providers (DeepSeek, OpenAI, Anthropic) implement the `Provider` trait.
//! Multiple ModelAdapter nodes can run simultaneously, each serving a
//! different model/provider.

mod error;
mod types;

pub use error::ProviderError;
pub use types::{
    ModelCallPayload, ModelParams, ModelResponsePayload, ToolCall, ToolDef, Usage,
};
```

逐行：
- `//!` 模块级 doc comment — 描述整个 crate 的用途。在 `cargo doc` 中作为 crate 首页。强调"被动节点"和"可插拔"两个核心设计理念
- `mod error; mod types;` — 声明子模块。Rust 的模块树从 `lib.rs` 开始，`pub use` 控制对外 API
- `pub use` — 重新导出（re-export）。外部 crate 只需 `use arf_model_adapter::ModelCallPayload`，无需关心内部模块结构。不导出内部 helper 函数（未来添加）——只暴露稳定 API 面
- 导出的 6 个类型是 Engine 和 ModelAdapter node 之间的契约。Provider trait（4.2）和具体 Provider 实现（4.3–4.5）后续追加

---

### 根 `Cargo.toml` — workspace members 追加

```toml
[workspace]
members = [
    "crates/arf-core",
    "crates/arf-bus",
    "crates/arf-state",
    "crates/arf-engine",
    "crates/arf-agent",
    "crates/arf-model-adapter",
    "py-arf",
]
```

逐行：
- `"crates/arf-model-adapter"` — 注册新 crate 到 workspace。加入后 `cargo test --workspace` 自动包含此 crate，`cargo fmt --all` 自动格式化其代码
- 位置在 `arf-agent` 之后、`py-arf` 之前，与依赖顺序大致一致（core → bus → state → engine → agent → model-adapter）

---

## 测试

脚手架阶段仅验证类型可构造、可序列化往返。边界标注沿用 Phase 2/3 约定：`[构造]` `[序列化]` `[trait]`。

### types.rs — 12 tests

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use arf_core::ModelMessage;

    // ═══════════════════════════════════════════════════════════
    // ModelParams — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [构造] 所有字段显式赋值可读
    #[test]
    fn model_params_all_fields() {
        let p = ModelParams {
            temperature: Some(0.7),
            max_tokens: Some(4096),
            thinking_enabled: true,
            extra: serde_json::json!({"reasoning_effort": "high"}),
        };
        assert_eq!(p.temperature, Some(0.7));
        assert!(p.thinking_enabled);
    }

    // [序列化] None 和 false 正确往返
    #[test]
    fn model_params_serialization_roundtrip() {
        let p = ModelParams {
            temperature: None,
            max_tokens: None,
            thinking_enabled: false,
            extra: serde_json::Value::Null,
        };
        let json = serde_json::to_string(&p).unwrap();
        let back: ModelParams = serde_json::from_str(&json).unwrap();
        assert_eq!(back.temperature, None);
        assert!(!back.thinking_enabled);
    }
```

逐测试：
- `model_params_all_fields` — 验证构造器无 panic。覆盖 `[构造]` 角度：所有字段正常值
- `model_params_serialization_roundtrip` — 验证 `None` 和 `false` 在序列化/反序列化中不丢失。覆盖 `[序列化]` 角度：空值往返

```rust
    // ═══════════════════════════════════════════════════════════
    // ToolDef — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [序列化] ToolDef 含复杂 parameters JSON 往返
    #[test]
    fn tool_def_serialization_roundtrip() {
        let t = ToolDef {
            name: "read_file".into(),
            description: "Read a file".into(),
            parameters: serde_json::json!({"type": "object"}),
        };
        let json = serde_json::to_string(&t).unwrap();
        let back: ToolDef = serde_json::from_str(&json).unwrap();
        assert_eq!(back.name, "read_file");
        assert_eq!(back.description, "Read a file");
    }

    // [trait] Clone 后值相等
    #[test]
    fn tool_def_clone() {
        let t = ToolDef {
            name: "t".into(),
            description: "d".into(),
            parameters: serde_json::json!({}),
        };
        assert_eq!(t.name, t.clone().name);
    }
```

逐测试：
- `tool_def_serialization_roundtrip` — `parameters` 为 JSON Schema 对象，验证嵌套 JSON 不丢失。覆盖 `[序列化]` 角度：复杂 JSON 值
- `tool_def_clone` — 验证 `#[derive(Clone)]` 正确工作。覆盖 `[trait]` 角度

```rust
    // ═══════════════════════════════════════════════════════════
    // ModelCallPayload — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [序列化] 含 messages + params 往返
    #[test]
    fn model_call_payload_serialization_roundtrip() {
        let payload = ModelCallPayload {
            messages: vec![ModelMessage::new("user", "hello")],
            tools: vec![],
            model_params: ModelParams {
                temperature: Some(0.5),
                max_tokens: None,
                thinking_enabled: false,
                extra: serde_json::Value::Null,
            },
        };
        let json = serde_json::to_string(&payload).unwrap();
        let back: ModelCallPayload = serde_json::from_str(&json).unwrap();
        assert_eq!(back.messages.len(), 1);
        assert_eq!(back.model_params.temperature, Some(0.5));
    }

    // [构造] tools 非空时正确存储
    #[test]
    fn model_call_payload_with_tools() {
        let payload = ModelCallPayload {
            messages: vec![],
            tools: vec![ToolDef {
                name: "search".into(),
                description: "Search the web".into(),
                parameters: serde_json::json!({"type": "object"}),
            }],
            model_params: ModelParams {
                temperature: None,
                max_tokens: None,
                thinking_enabled: false,
                extra: serde_json::Value::Null,
            },
        };
        assert_eq!(payload.tools.len(), 1);
        assert_eq!(payload.tools[0].name, "search");
    }
```

逐测试：
- `model_call_payload_serialization_roundtrip` — 验证聚合 struct 序列化：`messages` 嵌套 `ModelMessage`（来自 arf-core），`tools` 为空 Vec。覆盖 `[序列化]` 角度：嵌套类型往返
- `model_call_payload_with_tools` — 验证 `tools` 非空场景。覆盖 `[构造]` 角度：含工具定义的 payload

```rust
    // ═══════════════════════════════════════════════════════════
    // ModelResponsePayload — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [构造] 纯文本回复：无 tool_calls，有 usage
    #[test]
    fn model_response_payload_text_only() {
        let payload = ModelResponsePayload {
            message: ModelMessage::new("assistant", "Hello!"),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage { input_tokens: 10, output_tokens: 5, total_tokens: 15 }),
            id: "chatcmpl-123".into(),
            model: "deepseek-v4-flash".into(),
        };
        assert_eq!(payload.message.content, "Hello!");
        assert_eq!(payload.finish_reason, "stop");
        assert!(payload.tool_calls.is_none());
    }

    // [构造] 工具调用回复：有 tool_calls，finish_reason="tool_calls"
    #[test]
    fn model_response_payload_with_tool_calls() {
        let payload = ModelResponsePayload {
            message: ModelMessage::new("assistant", ""),
            tool_calls: Some(vec![ToolCall {
                id: "call_1".into(),
                name: "read_file".into(),
                arguments: serde_json::json!({"path": "/x"}),
            }]),
            finish_reason: "tool_calls".into(),
            usage: None,
            id: "chatcmpl-456".into(),
            model: "gpt-4o".into(),
        };
        let tc = payload.tool_calls.unwrap();
        assert_eq!(tc.len(), 1);
        assert_eq!(tc[0].name, "read_file");
        assert_eq!(tc[0].arguments["path"], "/x");
    }
```

逐测试：
- `model_response_payload_text_only` — 覆盖最常见的场景：模型返回纯文本，无工具调用。覆盖 `[构造]` 角度
- `model_response_payload_with_tool_calls` — 覆盖 function calling 场景：`tool_calls` 非空，`finish_reason = "tool_calls"`。验证 `arguments` 为嵌套 JSON 可正确读取。覆盖 `[构造]` 角度

```rust
    // ═══════════════════════════════════════════════════════════
    // ToolCall — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [序列化] ToolCall 含嵌套 arguments JSON 往返
    #[test]
    fn tool_call_serialization_roundtrip() {
        let tc = ToolCall {
            id: "call_abc".into(),
            name: "search".into(),
            arguments: serde_json::json!({"query": "rust"}),
        };
        let json = serde_json::to_string(&tc).unwrap();
        let back: ToolCall = serde_json::from_str(&json).unwrap();
        assert_eq!(back.id, "call_abc");
        assert_eq!(back.name, "search");
    }

    // [trait] Clone 后值相等
    #[test]
    fn tool_call_clone() {
        let tc = ToolCall {
            id: "x".into(),
            name: "y".into(),
            arguments: serde_json::json!({}),
        };
        assert_eq!(tc.id, tc.clone().id);
    }
```

```rust
    // ═══════════════════════════════════════════════════════════
    // Usage — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [序列化] Usage 往返
    #[test]
    fn usage_serialization_roundtrip() {
        let u = Usage { input_tokens: 100, output_tokens: 50, total_tokens: 150 };
        let json = serde_json::to_string(&u).unwrap();
        let back: Usage = serde_json::from_str(&json).unwrap();
        assert_eq!(back.total_tokens, 150);
    }

    // [边界] 零 token 合法（流式中间 chunk 或错误响应）
    #[test]
    fn usage_zero_tokens() {
        let u = Usage { input_tokens: 0, output_tokens: 0, total_tokens: 0 };
        assert_eq!(u.total_tokens, 0);
    }
}
```

逐测试：
- `usage_zero_tokens` — 覆盖 `[边界]` 角度：全零 usage 合法。流式响应的中间 chunk 可能不含 usage 信息，此时 Engine 可能构造零值 Usage 或 `None`。这里验证零值不 panic

---

### error.rs — 3 tests

```rust
#[cfg(test)]
mod tests {
    use super::*;

    // [trait] Transport 变体 Display 包含原始错误信息
    #[test]
    fn provider_error_display_transport() {
        let e = ProviderError::Transport("connection refused".into());
        assert!(format!("{e}").contains("connection refused"));
    }

    // [trait] Api 变体 Display 包含状态码和消息
    #[test]
    fn provider_error_display_api() {
        let e = ProviderError::Api { status: 401, message: "Unauthorized".into() };
        assert!(format!("{e}").contains("401"));
        assert!(format!("{e}").contains("Unauthorized"));
    }

    // [trait] ProviderError 实现 std::error::Error
    #[test]
    fn provider_error_implements_std_error() {
        fn takes_error(_e: impl std::error::Error) {}
        takes_error(ProviderError::Parse("test".into()));
    }
}
```

逐测试：
- `provider_error_display_transport` — 验证 `Transport` 变体的错误消息可读。覆盖 `[trait]` 角度：Display
- `provider_error_display_api` — 验证 `Api` 变体同时输出状态码和消息。状态码帮助排查，消息提供上下文
- `provider_error_implements_std_error` — 编译期验证 `ProviderError` 满足 `std::error::Error` trait。不测试运行时行为——只测试"能否作为 `impl Error` 传递"

---

## 测试汇总

| 类型 | 测试数 | 覆盖角度 |
|------|--------|---------|
| ModelParams | 2 | 构造、序列化 |
| ToolDef | 2 | 序列化、Clone |
| ModelCallPayload | 2 | 序列化、构造含工具 |
| ModelResponsePayload | 2 | 构造纯文本、构造工具调用 |
| ToolCall | 2 | 序列化、Clone |
| Usage | 2 | 序列化、边界(零值) |
| ProviderError | 3 | Display(×2)、std::error::Error |
| **合计** | **15** | |

---

## 交付标准

- `cargo test --workspace` 全部通过（238 + 15 = 253 tests）
- `cargo fmt --check` + `cargo clippy` 无警告
- 所有类型 serde 往返一致
