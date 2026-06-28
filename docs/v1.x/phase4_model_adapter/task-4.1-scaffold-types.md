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
reqwest = { version = "0.12", features = ["json"] }
tokio = { version = "1", features = ["sync", "rt", "macros"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

### `crates/arf-model-adapter/src/types.rs`

```rust
//! Shared types for ModelAdapter — payloads, params, tool defs, responses.

use arf_core::ModelMessage;
use serde::{Deserialize, Serialize};

/// Model inference parameters extracted from ModelSpec.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelParams {
    pub temperature: Option<f32>,
    pub max_tokens: Option<u32>,
    pub thinking_enabled: bool,
    pub extra: serde_json::Value,
}

/// Tool definition sent to the model for function calling.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDef {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,
}

/// Payload of a `model_call` Bus message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelCallPayload {
    pub messages: Vec<ModelMessage>,
    pub tools: Vec<ToolDef>,
    pub model_params: ModelParams,
}

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

/// A tool call request from the model.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: serde_json::Value,
}

/// Token usage statistics.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: u32,
    pub output_tokens: u32,
    pub total_tokens: u32,
}
```

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

---

## 测试

脚手架阶段仅验证类型可构造、可序列化往返。

### types.rs — 12 tests

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use arf_core::ModelMessage;

    // ═══════════════════════════════════════════════════════════
    // ModelParams — 2 tests
    // ═══════════════════════════════════════════════════════════

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

    // ═══════════════════════════════════════════════════════════
    // ToolDef — 2 tests
    // ═══════════════════════════════════════════════════════════

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

    #[test]
    fn tool_def_clone() {
        let t = ToolDef {
            name: "t".into(),
            description: "d".into(),
            parameters: serde_json::json!({}),
        };
        assert_eq!(t.name, t.clone().name);
    }

    // ═══════════════════════════════════════════════════════════
    // ModelCallPayload — 2 tests
    // ═══════════════════════════════════════════════════════════

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

    // ═══════════════════════════════════════════════════════════
    // ModelResponsePayload — 2 tests
    // ═══════════════════════════════════════════════════════════

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

    // ═══════════════════════════════════════════════════════════
    // ToolCall — 2 tests
    // ═══════════════════════════════════════════════════════════

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

    #[test]
    fn tool_call_clone() {
        let tc = ToolCall {
            id: "x".into(),
            name: "y".into(),
            arguments: serde_json::json!({}),
        };
        assert_eq!(tc.id, tc.clone().id);
    }

    // ═══════════════════════════════════════════════════════════
    // Usage — 2 tests
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn usage_serialization_roundtrip() {
        let u = Usage { input_tokens: 100, output_tokens: 50, total_tokens: 150 };
        let json = serde_json::to_string(&u).unwrap();
        let back: Usage = serde_json::from_str(&json).unwrap();
        assert_eq!(back.total_tokens, 150);
    }

    #[test]
    fn usage_zero_tokens() {
        let u = Usage { input_tokens: 0, output_tokens: 0, total_tokens: 0 };
        assert_eq!(u.total_tokens, 0);
    }
}
```

### error.rs — 3 tests

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provider_error_display_transport() {
        let e = ProviderError::Transport("connection refused".into());
        assert!(format!("{e}").contains("connection refused"));
    }

    #[test]
    fn provider_error_display_api() {
        let e = ProviderError::Api { status: 401, message: "Unauthorized".into() };
        assert!(format!("{e}").contains("401"));
        assert!(format!("{e}").contains("Unauthorized"));
    }

    #[test]
    fn provider_error_implements_std_error() {
        fn takes_error(_e: impl std::error::Error) {}
        takes_error(ProviderError::Parse("test".into()));
    }
}
```

---

## 测试汇总

| 类型 | 测试数 |
|------|--------|
| ModelParams | 2 |
| ToolDef | 2 |
| ModelCallPayload | 2 |
| ModelResponsePayload | 2 |
| ToolCall | 2 |
| Usage | 2 |
| ProviderError | 3 |
| **合计** | **15** |

---

## 交付标准

- `cargo test --workspace` 全部通过（238 + 15 = 253 tests）
- `cargo fmt --check` + `cargo clippy` 无警告
- 所有类型 serde 往返一致
