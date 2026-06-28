# 任务 4.4：OpenAI provider 实现

> Phase 4 — ModelAdapter 第四项任务
> 父文档：`docs/v1.x/phase4_model_adapter/phase4-model-adapter-design.md`
> 依赖：4.1（类型）+ 4.2（Provider trait），已完成

## 设计思路

`OpenAIProvider` 是 **OpenAI 兼容格式的基准实现**。所有使用 OpenAI-compatible `/v1/chat/completions` 端点的供应商（DeepSeek、Groq、Together AI 等）都共享同一套消息格式。OpenAI provider 实现这套标准格式，后续供应商只需在此基础叠加特有参数。

与 DeepSeek provider 的关键差异：
- **无 thinking 模式** — OpenAI 标准 API 不原生支持
- **无 reasoning_content 透传** — 消息转换不做此字段

两者共享的 SSE 解析、重试逻辑在本次一并提取到 `convert.rs`，消除重复代码。

### `convert.rs` 共享模块

提取 DeepSeek 和 OpenAI 共用的逻辑：

| 函数 | 来源 | 用途 |
|------|------|------|
| `parse_sse()` | 从 deepseek.rs 移出（保持相同） | SSE `data:` 行解析，5 种 chunk type |
| `is_retryable()` | 从 deepseek.rs 移出（保持相同） | 判断 ProviderError 是否可重试 |
| `call_with_retry()` | 泛化参数 | 通用指数退避重试 |

`call_with_retry()` 接受一个 `async fn` 闭包作为"单次尝试"操作，不再耦合 DeepSeek 的 `send_request`。DeepSeek refactor 为调用这些共享函数。

## 代码实现

### `crates/arf-model-adapter/src/openai.rs`（新文件）

```rust
//! OpenAI provider — standard OpenAI-compatible chat completions API.
//!
//! Endpoint: `POST /v1/chat/completions`
//! Auth: Bearer token
//!
//! This is the canonical implementation for all OpenAI-compatible providers.
//! Providers with extra features (e.g., DeepSeek thinking mode) extend from
//! this format.

use std::time::Duration;

use async_trait::async_trait;
use reqwest::Client;
use serde::Deserialize;
use serde_json::Value;

use arf_core::ModelMessage;

use crate::convert;
use crate::error::ProviderError;
use crate::provider::Provider;
use crate::types::{
    ModelParams, ModelResponseChunk, ModelResponsePayload, ToolCall, ToolDef, Usage,
};
```

逐行：
- `use crate::convert` — 共享的 SSE 解析、重试逻辑，DeepSeek 已提取到此处，OpenAI 直接复用
- 其余依赖同 DeepSeek provider

---

#### OpenAIConfig

```rust
/// Configuration for an OpenAI provider.
#[derive(Debug, Clone)]
pub struct OpenAIConfig {
    /// API base URL. Default: "https://api.openai.com".
    pub base_url: String,
    /// API key.
    pub api_key: String,
    /// Supported models (e.g., ["gpt-4o", "gpt-4-turbo"]).
    pub models: Vec<String>,
    /// Request timeout in seconds. Default: 320.
    pub timeout_secs: u64,
    /// Max retries for retryable errors. Default: 3.
    pub max_retries: u32,
}

impl OpenAIConfig {
    pub fn new(api_key: String, models: Vec<String>) -> Self {
        Self {
            base_url: "https://api.openai.com".into(),
            api_key,
            models,
            timeout_secs: 320,
            max_retries: 3,
        }
    }
}
```

逐行：
- `base_url` — 默认 `https://api.openai.com`，用户可覆盖指向其他 OpenAI 兼容服务（如 Azure OpenAI、local LLM）
- `api_key` — `sk-` 前缀
- `models` — 该 provider 支持的模型
- `timeout_secs` / `max_retries` — 与 DeepSeek 一致

---

#### OpenAIProvider

```rust
/// OpenAI API provider — standard chat completions.
pub struct OpenAIProvider {
    config: OpenAIConfig,
    client: Client,
}

impl OpenAIProvider {
    pub fn new(config: OpenAIConfig) -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(config.timeout_secs))
            .build()
            .expect("reqwest client should always build");
        Self { config, client }
    }

    fn endpoint(&self) -> String {
        format!("{}/v1/chat/completions", self.config.base_url)
    }

    /// Single HTTP call.
    async fn send_request(&self, body: &Value) -> Result<String, ProviderError> {
        let response = self
            .client
            .post(self.endpoint())
            .header("Authorization", format!("Bearer {}", self.config.api_key))
            .header("Content-Type", "application/json")
            .json(body)
            .send()
            .await
            .map_err(|e| ProviderError::Transport(e.to_string()))?;

        let status = response.status();
        let text = response.text().await
            .map_err(|e| ProviderError::Transport(e.to_string()))?;

        if status.is_success() {
            Ok(text)
        } else {
            Err(ProviderError::Api {
                status: status.as_u16(),
                message: text,
            })
        }
    }
}
```

逐行：
- `endpoint()` — OpenAI 标准端点 `/v1/chat/completions`，区别于 DeepSeek 的 `/chat/completions`
- `send_request()` — 与 DeepSeek 完全相同。后续可提取到 convert.rs 进一步消除重复

---

#### Message 转换（ARF → OpenAI 格式）

```rust
/// Convert ARF ModelMessage to OpenAI API message format.
///
/// Straight passthrough for all roles. No reasoning_content handling
/// (OpenAI doesn't support thinking mode natively).
fn convert_message(msg: &ModelMessage) -> Value {
    let mut api_msg = serde_json::Map::new();
    api_msg.insert("role".into(), msg.role.clone().into());
    api_msg.insert("content".into(), msg.content.clone().into());

    if let Some(tc_id) = &msg.tool_call_id {
        api_msg.insert("tool_call_id".into(), tc_id.clone().into());
    }
    if let Some(name) = &msg.name {
        api_msg.insert("name".into(), name.clone().into());
    }

    Value::Object(api_msg)
}
```

逐行：
- 比 DeepSeek 的 `convert_message` 更简洁——不做 `reasoning_content` 透传。OpenAI 没有思考模式
- role/content/tool_call_id/name 直通，与 OpenAI API 字段一一对应

---

#### 请求体构造

```rust
fn build_request_body(
    model_name: &str,
    messages: &[ModelMessage],
    tools: &[ToolDef],
    params: &ModelParams,
    stream: bool,
) -> Value {
    let mut body = serde_json::Map::new();
    body.insert("model".into(), model_name.into());
    body.insert(
        "messages".into(),
        messages.iter().map(convert_message).collect(),
    );
    body.insert("stream".into(), stream.into());

    if !tools.is_empty() {
        body.insert(
            "tools".into(),
            tools.iter().map(|t| {
                serde_json::json!({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    }
                })
            }).collect(),
        );
    }

    if let Some(t) = params.temperature {
        body.insert("temperature".into(), t.into());
    }
    if let Some(mt) = params.max_tokens {
        body.insert("max_tokens".into(), mt.into());
    }

    // OpenAI doesn't support thinking mode — `thinking_enabled` is ignored
    // Provider-specific extra params are passed through extra
    if !params.extra.is_null() {
        // Merge safe extra params: top_p, frequency_penalty, presence_penalty, etc.
        if let Some(obj) = params.extra.as_object() {
            for (key, value) in obj {
                if key != "reasoning_effort" && key != "reasoning_content" {
                    body.insert(key.clone(), value.clone());
                }
            }
        }
    }

    Value::Object(body)
}
```

逐行：
- `extra` 处理 — 不同于 DeepSeek 将 `reasoning_effort` 映射为 `thinking` 对象，OpenAI 将 `extra` 中的常规参数（`top_p`、`frequency_penalty` 等）直接合并到请求体。过滤掉 DeepSeek 特有字段（`reasoning_effort`、`reasoning_content`）
- 其余逻辑与 DeepSeek `build_request_body` 相同

---

#### 响应解析

```rust
#[derive(Debug, Deserialize)]
struct ApiResponse {
    id: String,
    model: String,
    choices: Vec<ApiChoice>,
    usage: Option<ApiUsage>,
}

#[derive(Debug, Deserialize)]
struct ApiChoice {
    finish_reason: Option<String>,
    message: ApiMessage,
}

#[derive(Debug, Deserialize)]
struct ApiMessage {
    #[allow(dead_code)]
    role: String,
    content: Option<String>,
    #[serde(default)]
    tool_calls: Option<Vec<ApiToolCall>>,
}

#[derive(Debug, Deserialize)]
struct ApiToolCall {
    id: String,
    #[serde(rename = "type")]
    _type: String,
    function: ApiFunction,
}

#[derive(Debug, Deserialize)]
struct ApiFunction {
    name: String,
    arguments: String,
}

#[derive(Debug, Deserialize)]
struct ApiUsage {
    prompt_tokens: u32,
    completion_tokens: u32,
    total_tokens: u32,
}

fn parse_response(raw: &str) -> Result<ModelResponsePayload, ProviderError> {
    let api: ApiResponse =
        serde_json::from_str(raw).map_err(|e| ProviderError::Parse(e.to_string()))?;

    let choice = api.choices.into_iter().next()
        .ok_or_else(|| ProviderError::Parse("no choices in response".into()))?;

    let content = choice.message.content.unwrap_or_default();

    let tool_calls = choice.message.tool_calls.map(|tc_list| {
        tc_list.into_iter().map(|tc| {
            let args: Value = serde_json::from_str(&tc.function.arguments)
                .unwrap_or(Value::String(tc.function.arguments));
            ToolCall { id: tc.id, name: tc.function.name, arguments: args }
        }).collect()
    });

    let message = ModelMessage::new("assistant", content);
    // OpenAI doesn't return reasoning_content — no extra passthrough needed

    let usage = api.usage.map(|u| Usage {
        input_tokens: u.prompt_tokens,
        output_tokens: u.completion_tokens,
        total_tokens: u.total_tokens,
    });

    Ok(ModelResponsePayload {
        message,
        tool_calls,
        finish_reason: choice.finish_reason.unwrap_or_else(|| "unknown".into()),
        usage,
        id: api.id,
        model: api.model,
    })
}
```

逐行：
- 响应结构与 DeepSeek 完全相同——两者都遵循 OpenAI 响应格式
- 不做 `reasoning_content` 提取（OpenAI 不返回此字段）
- `ApiMessage` 不含 `reasoning_content` 字段

---

#### Provider trait 实现

```rust
#[async_trait]
impl Provider for OpenAIProvider {
    fn name(&self) -> &str {
        "openai"
    }

    fn supported_models(&self) -> &[String] {
        &self.config.models
    }

    async fn chat(
        &self,
        model_name: &str,
        messages: Vec<ModelMessage>,
        tools: Vec<ToolDef>,
        params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        let body = build_request_body(model_name, &messages, &tools, &params, false);
        convert::call_with_retry(
            || async { self.send_request(&body).await },
            |raw| parse_response(&raw),
            self.config.max_retries,
        ).await
    }

    async fn chat_stream(
        &self,
        model_name: &str,
        messages: Vec<ModelMessage>,
        tools: Vec<ToolDef>,
        params: ModelParams,
    ) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
        let body = build_request_body(model_name, &messages, &tools, &params, true);
        let response = self
            .client
            .post(self.endpoint())
            .header("Authorization", format!("Bearer {}", self.config.api_key))
            .header("Content-Type", "application/json")
            .header("Accept", "text/event-stream")
            .json(&body)
            .send()
            .await
            .map_err(|e| ProviderError::Transport(e.to_string()))?;

        let status = response.status();
        if !status.is_success() {
            let text = response.text().await.unwrap_or_default();
            return Err(ProviderError::Api { status: status.as_u16(), message: text });
        }

        let full_text = response.text().await
            .map_err(|e| ProviderError::Transport(e.to_string()))?;

        convert::parse_sse(&full_text)
    }
}
```

逐行：
- `name()` — 返回 `"openai"`，Engine 用此值匹配 `ModelSpec.provider`
- `chat()` — 使用共享的 `convert::call_with_retry()`，传入闭包做 HTTP 请求和响应解析。不再内联重试逻辑
- `chat_stream()` — 使用共享的 `convert::parse_sse()` 解析 SSE 流

---

### `convert.rs` — 从 DeepSeek 提取的共享逻辑

SSE 解析和重试逻辑从 `deepseek.rs` 移入 `convert.rs`，OpenAI 和 DeepSeek 共同调用。内容与 4.3 中 deepseek.rs 的 `parse_sse()` / `is_retryable()` 完全相同，外加泛化的 `call_with_retry()`：

```rust
/// Generic retry with exponential backoff.
pub(crate) async fn call_with_retry<F, G, T>(
    mut attempt: impl FnMut() -> F,
    parse: impl Fn(&str) -> Result<T, ProviderError>,
    max_retries: u32,
) -> Result<T, ProviderError>
where
    F: std::future::Future<Output = Result<String, ProviderError>>,
{
    let mut last_error = String::new();
    for attempt_n in 0..=max_retries {
        match attempt().await {
            Ok(raw) => return parse(&raw),
            Err(e) => {
                last_error = e.to_string();
                if !is_retryable(&e) || attempt_n == max_retries {
                    return Err(e);
                }
                let delay = 2u64.pow(attempt_n + 1);
                tokio::time::sleep(Duration::from_secs(delay)).await;
            }
        }
    }
    Err(ProviderError::RetryExhausted { attempts: max_retries + 1, last_error })
}
```

逐行：
- 泛型参数 `F` — 闭包的返回类型，`async fn` 展开为 `impl Future<Output = Result<String, ProviderError>>`
- `attempt` — 每次重试调用的闭包（HTTP 请求）
- `parse` — 成功后解析 raw response 的闭包
- `max_retries` — 配置传入，解耦 Provider 实现

---

## 测试

省略已覆盖的 SSE/重试测试（在 convert.rs 中），仅测试 OpenAI 特有的消息转换和请求体构造。

### openai.rs — 8 tests

| 分类 | 测试数 | 覆盖 |
|------|--------|------|
| 消息转换 | 4 | user/system/assistant/tool |
| 请求体 | 3 | 最简/含工具/含 extra 参数 |
| 响应解析 | 1 | 文本回复 |
| **新增** | **8** | |
| **累计** (4.1–4.4) | **45** | |

---

### `lib.rs` — 追加 openai 模块

```rust
mod openai;

pub use openai::{OpenAIConfig, OpenAIProvider};
```

---

## 后续重构

DeepSeek provider 改为调用 `convert.rs` 的共享函数 + 叠加 thinking 处理。在 4.3 完成后单独做一次清理 commit。

---

## 交付标准

- `cargo test --workspace` 全部通过（275 + 8 = 283 tests）
- `cargo clippy` 无警告
- OpenAI 消息转换正确（user/system/assistant/tool 直通）
- extra 参数透传正确（过滤 DeepSeek 特有字段）
- 共享 convert.rs 的 SSE/重试可被 DeepSeek 复用
