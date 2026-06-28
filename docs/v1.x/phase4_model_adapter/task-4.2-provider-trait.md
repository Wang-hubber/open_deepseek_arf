# 任务 4.2：Provider trait 定义

> Phase 4 — ModelAdapter 第二项任务
> 父文档：`docs/v1.x/phase4_model_adapter/phase4-model-adapter-design.md`
> 依赖：4.1（脚手架 + 类型定义，已完成）

## 设计思路

`Provider` trait 是 ModelAdapter 的核心抽象——每个供应商（DeepSeek、OpenAI、Anthropic）都实现这个 trait。新增供应商 = 新增一个 `impl Provider`，不影响任何已有代码。

trait 封装四个职责：

| 职责 | 方法 | 说明 |
|------|------|------|
| 身份声明 | `name()` | 供应商标识，用于 `node_online` 广播 |
| 模型列表 | `supported_models()` | 该 Provider 支持的模型名 |
| 非流式调用 | `chat()` | 完整请求-响应，`stream: false` 时使用 |
| 流式调用 | `chat_stream()` | SSE 流，逐 chunk 产出；有默认 fallback 到 `chat()` |

`chat_stream()` 提供默认实现——如果 Provider 未实现真正的 SSE，框架自动 fallback 到非流式，将完整响应包装为无 chunk 的流。这允许新 Provider 先做非流式，后续再优化流式。

## 代码实现

### `crates/arf-model-adapter/src/provider.rs`（新文件）

```rust
//! Provider trait — abstract a model provider's chat completion API.

use async_trait::async_trait;

use crate::types::{
    ModelCallPayload, ModelParams, ModelResponseChunk, ModelResponsePayload, ToolDef,
};
use crate::ProviderError;
use arf_core::ModelMessage;

/// Abstract a model provider's chat completion API.
///
/// Each provider (DeepSeek, OpenAI, Anthropic) implements this trait.
/// The trait is `Send + Sync` so it can be shared across tokio tasks.
///
/// # Pluggability
///
/// Adding a new provider means adding one `impl Provider` struct.
/// Nothing else in the codebase changes — ModelAdapter node, Engine,
/// and Bus are all provider-agnostic.
#[async_trait]
pub trait Provider: Send + Sync {
    /// Human-readable provider identifier: "deepseek", "openai", "anthropic".
    ///
    /// Used in `node_online` capabilities broadcast so Engine can match
    /// `ModelSpec.provider` against available model nodes on the Bus.
    fn name(&self) -> &str;

    /// Models this provider supports.
    ///
    /// Used in `node_online` capabilities broadcast so Engine can match
    /// `ModelSpec.model_name` against available models.
    fn supported_models(&self) -> &[String];

    /// Send a non-streaming chat completion request.
    ///
    /// The provider is responsible for:
    /// 1. Converting internal `messages` → provider API format
    /// 2. Building the HTTP request with `params` mapped to API params
    /// 3. Making the HTTP call (with retry on 429/5xx)
    /// 4. Converting the API response → `ModelResponsePayload`
    ///    (including `finish_reason` normalization)
    async fn chat(
        &self,
        model_name: &str,
        messages: Vec<ModelMessage>,
        tools: Vec<ToolDef>,
        params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError>;

    /// Send a streaming chat completion request.
    ///
    /// Yields chunks as they arrive from the provider's SSE stream,
    /// plus the final aggregated response for State storage.
    ///
    /// **Default implementation** falls back to `chat()`: returns an
    /// empty chunk list + the complete response. Providers that support
    /// true SSE (DeepSeek, OpenAI, Anthropic) SHOULD override this.
    async fn chat_stream(
        &self,
        model_name: &str,
        messages: Vec<ModelMessage>,
        tools: Vec<ToolDef>,
        params: ModelParams,
    ) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
        let response = self.chat(model_name, messages, tools, params).await?;
        Ok((vec![], response))
    }
}
```

逐行：
- `#[async_trait]` — Rust 原生 async fn in trait 尚不稳定，使用 `async-trait` crate。将 `async fn` 展开为返回 `Pin<Box<dyn Future<...>>>` 的普通方法，支持 trait object
- `trait Provider: Send + Sync` — 必须 `Send + Sync`：Provider 实例会被 `Arc` 包装，多个 tokio task 共享引用（listen loop 并发处理多个 model_call）。不满足 `Sync` 的 struct（如内部含 `RefCell`）编译期被拒绝
- `name() -> &str` — 返回静态字符串引用。典型实现：`"deepseek"`，不涉及堆分配
- `supported_models() -> &[String]` — 返回配置中声明的模型列表。Engine 用它与 `ModelSpec.model_name` 做匹配
- `chat()` — 核心方法。参数 `model_name` 允许一个 Provider 实例服务多个模型。返回 `ModelResponsePayload`（非流式，一次返回完整结果）
- `chat_stream()` — 返回 `(Vec<ModelResponseChunk>, ModelResponsePayload)`：chunk 列表供实时推送，最终 payload 供存入 State。默认实现 fallback 到 `chat()` → chunks 为空，payload 完整。Provider 可 override 接入真正的 SSE
- `model_name: &str` 参数 — 与 `supported_models()` 解耦。Provider 实例可能配置多个模型名（如 `["deepseek-v4-flash", "deepseek-v4-pro"]`），Engine 运行时指定用哪个

### `Cargo.toml` 追加依赖

```toml
[dependencies]
async-trait = "0.1"
```

逐行：
- `async-trait` — 极轻量，仅提供 `#[async_trait]` attribute macro。编译后零运行时开销（`Box<dyn Future>` 是 Rust 标准库类型）

## 为什么 trait 方法不用 `&self` + `ModelCallPayload` 聚合参数？

分离参数让 trait 使用者更清晰：
- `model_name` — 可能和 Provider 配置的默认模型不同（Engine 指定）
- `messages` — 对话历史，可能被过滤/截断后再传入
- `tools` — 可能被权限过滤后再传入
- `params` — 直接从 `ModelCallPayload` 解包传递

聚合在一起（传整个 `ModelCallPayload`）对 Provider 实现者更省事，但参数分离让调用方知道"传了什么"，便于在调用前做截断/过滤/校验。Engine 会在发送前对 `messages` 做 compaction。

---

## 测试

trait 本身无运行时行为——测试通过一个 mock Provider 验证 trait 可被实现、`chat_stream()` 默认 fallback 正确。

### provider.rs — 4 tests

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{ModelParams, ToolDef, Usage};
    use arf_core::ModelMessage;

    /// A minimal mock provider for testing the trait machinery.
    struct MockProvider {
        name: String,
        models: Vec<String>,
    }

    #[async_trait]
    impl Provider for MockProvider {
        fn name(&self) -> &str {
            &self.name
        }

        fn supported_models(&self) -> &[String] {
            &self.models
        }

        async fn chat(
            &self,
            _model_name: &str,
            _messages: Vec<ModelMessage>,
            _tools: Vec<ToolDef>,
            _params: ModelParams,
        ) -> Result<ModelResponsePayload, ProviderError> {
            Ok(ModelResponsePayload {
                message: ModelMessage::new("assistant", "mock reply"),
                tool_calls: None,
                finish_reason: "stop".into(),
                usage: Some(Usage {
                    input_tokens: 10,
                    output_tokens: 5,
                    total_tokens: 15,
                }),
                id: "mock-id".into(),
                model: "mock-model".into(),
            })
        }
    }

    fn mock_provider() -> MockProvider {
        MockProvider {
            name: "mock".into(),
            models: vec!["mock-model".into()],
        }
    }

    fn empty_params() -> ModelParams {
        ModelParams {
            temperature: None,
            max_tokens: None,
            thinking_enabled: false,
            extra: serde_json::Value::Null,
        }
    }

    // [构造] name() 返回配置的供应商名称
    #[test]
    fn provider_name() {
        let p = mock_provider();
        assert_eq!(p.name(), "mock");
    }

    // [构造] supported_models() 返回配置的模型列表
    #[test]
    fn provider_supported_models() {
        let p = mock_provider();
        assert_eq!(p.supported_models(), &["mock-model"]);
    }

    // [方法] chat() 返回完整 ModelResponsePayload
    #[tokio::test]
    async fn provider_chat_returns_response() {
        let p = mock_provider();
        let result = p
            .chat("mock-model", vec![], vec![], empty_params())
            .await
            .unwrap();
        assert_eq!(result.message.content, "mock reply");
        assert_eq!(result.finish_reason, "stop");
        assert!(result.usage.is_some());
    }

    // [方法] chat_stream() 默认 fallback 到 chat()，chunks 为空，payload 完整
    #[tokio::test]
    async fn provider_chat_stream_falls_back_to_chat() {
        let p = mock_provider();
        let (chunks, response) = p
            .chat_stream("mock-model", vec![], vec![], empty_params())
            .await
            .unwrap();
        // Default implementation: no chunks, full response
        assert!(chunks.is_empty());
        assert_eq!(response.message.content, "mock reply");
        assert_eq!(response.finish_reason, "stop");
    }
}
```

逐测试：
- `provider_name` — 验证 `name()` 返回构造时传入的值。覆盖 `[构造]` 角度
- `provider_supported_models` — 验证模型列表。覆盖 `[构造]` 角度
- `provider_chat_returns_response` — 端到端调用 `chat()`，验证返回结构完整。覆盖 `[方法]` 角度
- `provider_chat_stream_falls_back_to_chat` — **关键测试**：默认 `chat_stream()` 实现不 panic，返回空 chunks + 完整 payload。确保未实现 SSE 的 Provider 也能正常工作。覆盖 `[方法]` 角度

---

### `lib.rs` — 追加 provider 模块

```rust
mod error;
mod provider;    // 新增
mod types;

pub use error::ProviderError;
pub use provider::Provider;
pub use types::{
    ModelCallPayload, ModelParams, ModelResponseChunk, ModelResponsePayload, ToolCall,
    ToolCallDelta, ToolDef, Usage,
};
```

---

## 测试汇总

| 文件 | 测试数 | 覆盖角度 |
|------|--------|---------|
| provider.rs | 4 | 构造(×2)、方法(×2，含 fallback) |
| **累计** (4.1 + 4.2) | **23** | |

---

## 与后续任务的关系

| 任务 | 如何使用 Provider trait |
|------|----------------------|
| 4.3 DeepSeek | `DeepSeekProvider` 实现 `Provider`，覆盖 `chat()` 和 `chat_stream()` |
| 4.4 OpenAI | `OpenAIProvider` 实现 `Provider` |
| 4.5 Anthropic | `AnthropicProvider` 实现 `Provider` |
| 4.7 node | `ModelAdapterNode` 持有 `Arc<dyn Provider>`，通过 trait 方法调用 |

## 交付标准

- `cargo test --workspace` 全部通过（257 + 4 = 261 tests）
- `cargo fmt --check` + `cargo clippy` 无警告
- `Provider` trait 可 mock（已验证）
- `chat_stream()` 默认 fallback 正确
