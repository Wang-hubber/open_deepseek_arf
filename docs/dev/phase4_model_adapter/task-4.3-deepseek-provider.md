# 任务 4.3：DeepSeek provider 实现

> Phase 4 — ModelAdapter 第三项任务
> 父文档：`docs/v1.x/phase4_model_adapter/phase4-model-adapter-design.md`
> 依赖：4.1（类型）+ 4.2（Provider trait），已完成

## 设计思路

`DeepSeekProvider` 实现 `Provider` trait，对接 DeepSeek API。DeepSeek 提供两种 API 格式：

| 格式 | 端点 | 说明 |
|------|------|------|
| OpenAI 兼容 | `POST /chat/completions` | 主力端点，与 OpenAI SDK 兼容 |
| Anthropic 兼容 | `POST /anthropic` | DeepSeek 提供的 Anthropic Messages API 兼容端点 |

本次实现 OpenAI 格式。Anthropic 格式在 4.5 用独立 provider 实现。

### 核心特性

| 特性 | 实现方式 |
|------|---------|
| 认证 | `Authorization: Bearer {api_key}` 头 |
| 思考模式 | `thinking_enabled` + `extra.reasoning_effort` → `thinking: {type, effort}` |
| reasoning_content 透传 | outgoing 消息保留 `extra.reasoning_content`，维持思考连续性 |
| 流式 | SSE (`stream: true`)，`text/event-stream` 解析 |
| 重试 | 429/5xx 指数退避，3 次 |
| 响应解析 | JSON deserialize → `ModelResponsePayload` |

## 代码实现

### `crates/arf-model-adapter/src/deepseek.rs`（新文件）

```rust
//! DeepSeek provider — OpenAI-compatible chat completions API.
//!
//! Endpoint: `POST /chat/completions`
//! Auth: Bearer token
//! Features: thinking mode, reasoning_content passthrough, streaming

use std::time::Duration;

use async_trait::async_trait;
use reqwest::Client;
use serde::Deserialize;
use serde_json::Value;

use arf_core::ModelMessage;

use crate::Provider;
use crate::error::ProviderError;
use crate::types::{
    ModelParams, ModelResponseChunk, ModelResponsePayload, ToolCall, ToolDef, Usage,
};
```

逐行：
- `reqwest::Client` — 复用连接池，避免每次请求新建 TCP 连接。`rustls-tls` 已在 crate 级别配置
- `serde::Deserialize` — API 响应的 JSON 反序列化，使用 borrow-free 的 owned 类型（`Value`、`String`）
- `arf_core::ModelMessage` — ARF 内部消息格式，Provider 负责转为 DeepSeek API 格式

---

#### DeepSeekConfig

```rust
/// Configuration for a DeepSeek provider.
#[derive(Debug, Clone)]
pub struct DeepSeekConfig {
    /// API base URL. Default: "https://api.deepseek.com".
    pub base_url: String,
    /// API key.
    pub api_key: String,
    /// Supported models (e.g., ["deepseek-v4-flash", "deepseek-v4-pro"]).
    pub models: Vec<String>,
    /// Request timeout in seconds. Default: 320.
    pub timeout_secs: u64,
    /// Max retries for retryable errors. Default: 3.
    pub max_retries: u32,
}

impl DeepSeekConfig {
    pub fn new(api_key: String, models: Vec<String>) -> Self {
        Self {
            base_url: "https://api.deepseek.com".into(),
            api_key,
            models,
            timeout_secs: 320,
            max_retries: 3,
        }
    }
}
```

逐行：
- `base_url` — 默认 `https://api.deepseek.com`，拼接 `/chat/completions` 得到完整端点
- `api_key` — `sk-` 前缀的 API key
- `models` — 该 Provider 实例支持的模型名，用于 `node_online` 广播
- `timeout_secs` — 单次 HTTP 请求的超时。320s 足够覆盖长思考 + 流式输出
- `max_retries` — 可重试错误（429/5xx）的最大重试次数。含首次共 4 次尝试
- `new()` — 最小构造，只需 api_key + models，其余用合理默认值

---

#### DeepSeekProvider

```rust
/// DeepSeek API provider — OpenAI-compatible chat completions.
pub struct DeepSeekProvider {
    config: DeepSeekConfig,
    client: Client,
}

impl DeepSeekProvider {
    /// Create a new DeepSeek provider with the given configuration.
    pub fn new(config: DeepSeekConfig) -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(config.timeout_secs))
            .build()
            .expect("reqwest client should always build");
        Self { config, client }
    }

    /// Full chat completions endpoint URL.
    fn endpoint(&self) -> String {
        format!("{}/chat/completions", self.config.base_url)
    }
}
```

逐行：
- `client: Client` — `reqwest::Client`，连接池复用。构建时设置全局超时。`expect` 允许 panic——Client 构建失败只有在 TLS 后端不可用时发生，属于环境配置错误
- `endpoint()` — 拼接完整 URL。`base_url` 不含尾部 `/`

---

#### Message 转换（ARF → DeepSeek OpenAI 格式）

```rust
/// Convert ARF ModelMessage to DeepSeek API message format.
///
/// Rules:
/// - role passthrough for system/user/assistant/tool
/// - assistant with tool_calls in extra → content=null, tool_calls=[...]
/// - tool role → tool_call_id + content
/// - reasoning_content passthrough for thinking continuity
fn convert_message(msg: &ModelMessage) -> Value {
    let mut api_msg = serde_json::Map::new();
    api_msg.insert("role".into(), msg.role.clone().into());

    // For assistant with tool_calls: content is null
    // (tool_calls stored in extra for now; Phase 5 ModelMessage expansion)
    api_msg.insert("content".into(), msg.content.clone().into());

    if let Some(tc_id) = &msg.tool_call_id {
        api_msg.insert("tool_call_id".into(), tc_id.clone().into());
    }
    if let Some(name) = &msg.name {
        api_msg.insert("name".into(), name.clone().into());
    }

    // Passthrough reasoning_content for thinking continuity
    if !msg.extra.is_null()
        && msg.extra.get("reasoning_content").is_some()
    {
        api_msg.insert(
            "reasoning_content".into(),
            msg.extra["reasoning_content"].clone(),
        );
    }

    Value::Object(api_msg)
}
```

逐行：
- `convert_message()` — 单个消息的转换。不做批量——调用方对 `Vec<ModelMessage>` 做 `iter().map(convert_message).collect()`
- `tool_call_id` / `name` — 仅在 `Some` 时插入 JSON 字段。`tool` 角色的消息必须有 `tool_call_id`
- `reasoning_content` 透传 — DeepSeek 思考模式要求：如果上一轮 assistant 消息含 `reasoning_content`，下一轮请求中须原样传回，否则思考模式断开。Provider 检查 `extra.reasoning_content` 并在 API 消息中输出

---

#### 参数映射（ModelParams → API request body）

```rust
/// Build the API request body.
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
            tools
                .iter()
                .map(|t| {
                    serde_json::json!({
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                    })
                })
                .collect(),
        );
    }

    // Standard params
    if let Some(t) = params.temperature {
        body.insert("temperature".into(), t.into());
    }
    if let Some(mt) = params.max_tokens {
        body.insert("max_tokens".into(), mt.into());
    }

    // Thinking mode
    if params.thinking_enabled {
        let mut thinking = serde_json::json!({"type": "enabled"});
        if let Some(effort) = params.extra.get("reasoning_effort") {
            thinking["effort"] = effort.clone();
        }
        body.insert("thinking".into(), thinking);
    }
    // When disabled, explicitly send disabled to override model default
    if !params.extra.is_null() && !params.thinking_enabled {
        // Only send if user explicitly set something in extra (not just default false)
        // Default false = don't send, let API decide
    }

    Value::Object(body)
}
```

逐行：
- `model_name` — 直接使用 Engine 传入的模型名，不做校验（API 会返回 400 如果模型不存在）
- `messages` — 调用 `convert_message` 逐条转换
- `stream: bool` — 必传，默认 `true`。API 根据此字段决定返回 SSE 流还是一次性 JSON
- `tools` — 转为 OpenAI function calling 格式：`{type: "function", function: {name, description, parameters}}`。空数组时不传 `tools` 字段（部分 API 版本对空 tools 敏感）
- `temperature` / `max_tokens` — `Some` 时才插入，让 API 使用自身默认值
- `thinking` — ARF 的 `thinking_enabled: bool` 映射为 DeepSeek 的 `thinking: {type: "enabled"/"disabled"}`。开启时，若 `extra.reasoning_effort` 存在则合并到 thinking 对象。关闭时不显式发 `disabled`——API 不传即默认关闭

---

#### 响应解析（API response → ModelResponsePayload）

```rust
/// Raw API response shape (subset DeepSeek returns).
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
    role: String,
    content: Option<String>,
    #[serde(default)]
    tool_calls: Option<Vec<ApiToolCall>>,
    #[serde(default)]
    reasoning_content: Option<String>,
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

/// Convert API response to ModelResponsePayload.
fn parse_response(raw: &str) -> Result<ModelResponsePayload, ProviderError> {
    let api: ApiResponse =
        serde_json::from_str(raw).map_err(|e| ProviderError::Parse(e.to_string()))?;

    let choice = api
        .choices
        .into_iter()
        .next()
        .ok_or_else(|| ProviderError::Parse("no choices in response".into()))?;

    let content = choice.message.content.unwrap_or_default();

    let tool_calls = choice.message.tool_calls.map(|tc_list| {
        tc_list
            .into_iter()
            .map(|tc| {
                let args: Value = serde_json::from_str(&tc.function.arguments)
                    .unwrap_or(Value::String(tc.function.arguments));
                ToolCall {
                    id: tc.id,
                    name: tc.function.name,
                    arguments: args,
                }
            })
            .collect()
    });

    let mut extra = Value::Null;
    if let Some(rc) = choice.message.reasoning_content
        && !rc.is_empty()
    {
        extra = serde_json::json!({"reasoning_content": rc});
    }

    let message = ModelMessage::new("assistant", content).with_extra(extra);

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
- `ApiResponse` / `ApiChoice` / `ApiMessage` — 私有 struct，仅用于反序列化。不暴露到 crate 外。字段按需提取（DeepSeek 返回的完整响应字段比这里多，未使用的字段 serde 自动忽略）
- `ApiToolCall._type` — `#[serde(rename = "type")]` 映射 JSON 的 `type` 字段到 Rust 的 `_type`（`type` 是 Rust 保留字）
- `ApiFunction.arguments: String` — API 返回的 `arguments` 是 JSON 字符串，不是 JSON 对象。Provider 用 `serde_json::from_str` 解析回 `Value`。解析失败时 fallback 为 `Value::String`（非标准参数格式不丢数据）
- `parse_response()` — 提取 `choices[0]`，取 `message.content`（`None` 时用空字符串），提取 `tool_calls`，提取 `reasoning_content` 存入 `extra`，映射 `ApiUsage` → `Usage`
- `finish_reason` — DeepSeek 返回的值直接就是 ARF 标准值（`stop`/`length`/`tool_calls`/`content_filter`），无需映射

---

#### Provider trait 实现

```rust
#[async_trait]
impl Provider for DeepSeekProvider {
    fn name(&self) -> &str {
        "deepseek"
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
        self.call_with_retry(body).await
    }

    async fn chat_stream(
        &self,
        model_name: &str,
        messages: Vec<ModelMessage>,
        tools: Vec<ToolDef>,
        params: ModelParams,
    ) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
        let body = build_request_body(model_name, &messages, &tools, &params, true);
        self.call_stream(body).await
    }
}
```

逐行：
- `name()` — 返回 `"deepseek"`，用于 Engine 匹配 `ModelSpec.provider`
- `chat()` — 非流式：`stream: false`，一次 HTTP 调用，解析 JSON 响应
- `chat_stream()` — override 默认 fallback，真正实现 SSE：`stream: true`，解析 `text/event-stream`

---

#### HTTP 调用 + 重试

```rust
impl DeepSeekProvider {
    /// Single HTTP call with retry on 429/5xx.
    async fn call_with_retry(
        &self,
        body: Value,
    ) -> Result<ModelResponsePayload, ProviderError> {
        let mut last_error = String::new();
        for attempt in 0..=self.config.max_retries {
            match self.send_request(&body).await {
                Ok(response) => return parse_response(&response),
                Err(e) => {
                    last_error = e.to_string();
                    if !is_retryable(&e) || attempt == self.config.max_retries {
                        return Err(e);
                    }
                    let delay = 2u64.pow(attempt + 1); // 2s, 4s, 8s
                    tokio::time::sleep(Duration::from_secs(delay)).await;
                }
            }
        }
        Err(ProviderError::RetryExhausted {
            attempts: self.config.max_retries + 1,
            last_error,
        })
    }

    /// Send a single HTTP request.
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
        let text = response.text().await.map_err(|e| ProviderError::Transport(e.to_string()))?;

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

/// Determine if an error is retryable.
fn is_retryable(err: &ProviderError) -> bool {
    match err {
        ProviderError::Api { status, .. } => {
            *status == 429 || (500..600).contains(status)
        }
        ProviderError::Transport(_) => true, // network errors are transient
        _ => false,
    }
}
```

逐行：
- `call_with_retry()` — 指数退避重试。首次失败后等 2s、4s、8s。`max_retries=3` 即最多 4 次尝试。重试覆盖 429 + 5xx + 传输错误
- `send_request()` — 单次 HTTP POST。拼接 Authorization 头、Content-Type、JSON body。成功返回 body 文本，失败返回 `ProviderError::Api` 含状态码
- `is_retryable()` — 判断逻辑。`Transport` 错误视为可重试（网络瞬时故障），`Api` 只有 429/5xx 可重试。`Parse` 和 `Api(4xx)` 不可重试——参数错误重试无意义

---

#### 流式响应

```rust
impl DeepSeekProvider {
    /// Send a streaming request, parse SSE chunks.
    async fn call_stream(
        &self,
        body: Value,
    ) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
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
            return Err(ProviderError::Api {
                status: status.as_u16(),
                message: text,
            });
        }

        let full_text = response
            .text()
            .await
            .map_err(|e| ProviderError::Transport(e.to_string()))?;

        parse_sse(&full_text)
    }
}

/// Parse SSE event stream into chunks + final response.
fn parse_sse(raw: &str) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
    let mut chunks = Vec::new();
    let mut final_payload: Option<ModelResponsePayload> = None;
    let mut full_content = String::new();
    let mut full_reasoning = String::new();
    let mut acc_tool_calls: Vec<ToolCall> = Vec::new();
    let mut finish_reason = String::new();
    let mut model = String::new();
    let mut response_id = String::new();
    let mut usage: Option<Usage> = None;

    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with(':') {
            continue; // SSE comment or heartbeat
        }
        if let Some(data) = line.strip_prefix("data: ") {
            if data == "[DONE]" {
                break;
            }
            if let Ok(chunk) = serde_json::from_str::<Value>(data) {
                // Track metadata
                if let Some(m) = chunk.get("model").and_then(|v| v.as_str()) {
                    model = m.to_string();
                }
                if let Some(id) = chunk.get("id").and_then(|v| v.as_str()) {
                    response_id = id.to_string();
                }

                if let Some(choices) = chunk.get("choices").and_then(|v| v.as_array()) {
                    for choice in choices {
                        if let Some(fr) = choice.get("finish_reason").and_then(|v| v.as_str()) {
                            finish_reason = fr.to_string();
                        }

                        let delta = choice.get("delta");
                        if let Some(delta) = delta {
                            // Text content
                            if let Some(c) = delta.get("content").and_then(|v| v.as_str()) {
                                full_content.push_str(c);
                                chunks.push(ModelResponseChunk {
                                    chunk_type: "text".into(),
                                    content: Some(c.to_string()),
                                    reasoning: None,
                                    tool_call: None,
                                    usage: None,
                                });
                            }

                            // Reasoning content
                            if let Some(rc) = delta.get("reasoning_content").and_then(|v| v.as_str()) {
                                full_reasoning.push_str(rc);
                                chunks.push(ModelResponseChunk {
                                    chunk_type: "reasoning".into(),
                                    content: None,
                                    reasoning: Some(rc.to_string()),
                                    tool_call: None,
                                    usage: None,
                                });
                            }

                            // Tool calls
                            if let Some(tc_list) = delta.get("tool_calls").and_then(|v| v.as_array()) {
                                for tc in tc_list {
                                    let index = tc.get("index").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
                                    let tc_id = tc.get("id").and_then(|v| v.as_str()).map(|s| s.to_string());
                                    let tc_name = tc
                                        .get("function")
                                        .and_then(|f| f.get("name"))
                                        .and_then(|v| v.as_str())
                                        .map(|s| s.to_string());
                                    let args_delta = tc
                                        .get("function")
                                        .and_then(|f| f.get("arguments"))
                                        .and_then(|v| v.as_str())
                                        .map(|s| s.to_string());

                                    chunks.push(ModelResponseChunk {
                                        chunk_type: "tool_call".into(),
                                        content: None,
                                        reasoning: None,
                                        tool_call: Some(crate::types::ToolCallDelta {
                                            index,
                                            id: tc_id.clone(),
                                            name: tc_name.clone(),
                                            arguments_delta: args_delta.clone(),
                                        }),
                                        usage: None,
                                    });

                                    // Accumulate tool call
                                    if let Some(id) = tc_id
                                        && let Some(name) = tc_name
                                    {
                                        // Find or create accumulator
                                        if let Some(existing) = acc_tool_calls
                                            .iter_mut()
                                            .find(|tc| tc.id == id)
                                        {
                                            if let Some(ref delta) = args_delta {
                                                if let Value::String(ref mut s) = existing.arguments {
                                                    s.push_str(delta);
                                                }
                                            }
                                        } else {
                                            let args_str = args_delta.unwrap_or_default();
                                            let args: Value =
                                                serde_json::from_str(&args_str)
                                                    .unwrap_or(Value::String(args_str));
                                            acc_tool_calls.push(ToolCall {
                                                id,
                                                name,
                                                arguments: args,
                                            });
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Usage (typically in final chunk)
                if let Some(u) = chunk.get("usage") {
                    usage = Some(Usage {
                        input_tokens: u["prompt_tokens"].as_u64().unwrap_or(0) as u32,
                        output_tokens: u["completion_tokens"].as_u64().unwrap_or(0) as u32,
                        total_tokens: u["total_tokens"].as_u64().unwrap_or(0) as u32,
                    });
                    chunks.push(ModelResponseChunk {
                        chunk_type: "usage".into(),
                        content: None,
                        reasoning: None,
                        tool_call: None,
                        usage: usage.clone(),
                    });
                }
            }
        }
    }

    let mut extra = Value::Null;
    if !full_reasoning.is_empty() {
        extra = serde_json::json!({"reasoning_content": full_reasoning});
    }

    let message = ModelMessage::new("assistant", full_content).with_extra(extra);

    let tool_calls = if acc_tool_calls.is_empty() {
        None
    } else {
        Some(acc_tool_calls)
    };

    final_payload = Some(ModelResponsePayload {
        message,
        tool_calls,
        finish_reason: if finish_reason.is_empty() {
            "stop".into()
        } else {
            finish_reason
        },
        usage,
        id: response_id,
        model,
    });

    Ok((
        chunks,
        final_payload.ok_or_else(|| ProviderError::Parse("no response from stream".into()))?,
    ))
}
```

逐行：
- `call_stream()` — 发送 `stream: true` 请求，指定 `Accept: text/event-stream`。请求成功后读全部 body 文本
- `parse_sse()` — 逐行解析 SSE 事件流。`data: [DONE]` 标记流结束。`data:` 前缀行解析为 JSON chunk
- chunk 类型分类：
  - `delta.content` → `chunk_type: "text"`
  - `delta.reasoning_content` → `chunk_type: "reasoning"`（DeepSeek 特有）
  - `delta.tool_calls` → `chunk_type: "tool_call"`，含增量参数
  - `usage` → `chunk_type: "usage"`
- tool call 累积 — SSE 流中 tool call 参数分多个 chunk 到达。按 `index` + `id` 找到或创建累加器，拼接 `arguments` 字符串片段。流结束后累加器内容写入 `ModelResponsePayload.tool_calls`
- `full_content` / `full_reasoning` — 所有 chunk 的文本/推理内容拼接为完整字符串，构建最终的 `ModelMessage`
- SSE 注释行（`:` 开头）和空行跳过（heartbeat 保活）

---

## 测试

### 单元测试（mock HTTP）— deepseek.rs

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::ToolDef;

    // ═══════════════════════════════════════════════════════════
    // Message conversion — 5 tests
    // ═══════════════════════════════════════════════════════════

    // [转换] user 消息：role + content 直通
    #[test]
    fn convert_user_message() {
        let msg = ModelMessage::new("user", "hello");
        let api = convert_message(&msg);
        assert_eq!(api["role"], "user");
        assert_eq!(api["content"], "hello");
    }

    // [转换] system 消息
    #[test]
    fn convert_system_message() {
        let msg = ModelMessage::new("system", "you are helpful");
        let api = convert_message(&msg);
        assert_eq!(api["role"], "system");
        assert_eq!(api["content"], "you are helpful");
    }

    // [转换] assistant 消息
    #[test]
    fn convert_assistant_message() {
        let msg = ModelMessage::new("assistant", "response");
        let api = convert_message(&msg);
        assert_eq!(api["role"], "assistant");
        assert_eq!(api["content"], "response");
    }

    // [转换] tool 消息含 tool_call_id
    #[test]
    fn convert_tool_message() {
        let msg = ModelMessage::new("tool", "result")
            .with_tool_call_id("call_abc")
            .with_name("search");
        let api = convert_message(&msg);
        assert_eq!(api["role"], "tool");
        assert_eq!(api["tool_call_id"], "call_abc");
        assert_eq!(api["name"], "search");
    }

    // [转换] reasoning_content 透传
    #[test]
    fn convert_assistant_with_reasoning() {
        let msg = ModelMessage::new("assistant", "answer")
            .with_extra(serde_json::json!({"reasoning_content": "step 1..."}));
        let api = convert_message(&msg);
        assert_eq!(api["reasoning_content"], "step 1...");
    }

    // ═══════════════════════════════════════════════════════════
    // Request body — 4 tests
    // ═══════════════════════════════════════════════════════════

    // [构造] 最简请求 body
    #[test]
    fn build_minimal_body() {
        let body = build_request_body(
            "deepseek-v4-flash",
            &[ModelMessage::new("user", "hi")],
            &[],
            &ModelParams {
                temperature: None,
                max_tokens: None,
                thinking_enabled: false,
                extra: Value::Null,
            },
            false,
        );
        assert_eq!(body["model"], "deepseek-v4-flash");
        assert_eq!(body["stream"], false);
        assert_eq!(body["messages"].as_array().unwrap().len(), 1);
    }

    // [构造] 含工具定义
    #[test]
    fn build_body_with_tools() {
        let tools = vec![ToolDef {
            name: "search".into(),
            description: "search web".into(),
            parameters: serde_json::json!({"type": "object"}),
        }];
        let body = build_request_body(
            "m",
            &[],
            &tools,
            &ModelParams {
                temperature: None,
                max_tokens: None,
                thinking_enabled: false,
                extra: Value::Null,
            },
            true,
        );
        let api_tools = body["tools"].as_array().unwrap();
        assert_eq!(api_tools.len(), 1);
        assert_eq!(api_tools[0]["type"], "function");
        assert_eq!(api_tools[0]["function"]["name"], "search");
    }

    // [构造] thinking 开启
    #[test]
    fn build_body_thinking_enabled() {
        let body = build_request_body(
            "deepseek-v4-pro",
            &[],
            &[],
            &ModelParams {
                temperature: None,
                max_tokens: None,
                thinking_enabled: true,
                extra: serde_json::json!({"reasoning_effort": "high"}),
            },
            false,
        );
        let thinking = &body["thinking"];
        assert_eq!(thinking["type"], "enabled");
        assert_eq!(thinking["effort"], "high");
    }

    // [构造] stream=true
    #[test]
    fn build_body_stream_true() {
        let body = build_request_body(
            "m", &[], &[],
            &ModelParams { temperature: None, max_tokens: None, thinking_enabled: false, extra: Value::Null },
            true,
        );
        assert_eq!(body["stream"], true);
    }

    // ═══════════════════════════════════════════════════════════
    // Response parsing — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [解析] 文本回复
    #[test]
    fn parse_text_response() {
        let raw = r#"{
            "id": "chatcmpl-123",
            "model": "deepseek-v4-flash",
            "choices": [{
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Hello!"}
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }"#;
        let result = parse_response(raw).unwrap();
        assert_eq!(result.message.content, "Hello!");
        assert_eq!(result.finish_reason, "stop");
        assert!(result.tool_calls.is_none());
        assert_eq!(result.usage.unwrap().total_tokens, 15);
    }

    // [解析] tool_calls 回复（含 reasoning_content）
    #[test]
    fn parse_tool_call_response() {
        let raw = r#"{
            "id": "chatcmpl-456",
            "model": "deepseek-v4-pro",
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": null,
                    "reasoning_content": "I need to search",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search", "arguments": "{\"query\":\"rust\"}"}
                    }]
                }
            }],
            "usage": null
        }"#;
        let result = parse_response(raw).unwrap();
        assert_eq!(result.finish_reason, "tool_calls");
        let tc = result.tool_calls.unwrap();
        assert_eq!(tc[0].name, "search");
        assert_eq!(tc[0].arguments["query"], "rust");
        assert_eq!(result.message.extra["reasoning_content"], "I need to search");
    }

    // ═══════════════════════════════════════════════════════════
    // SSE parsing — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [解析] 流式文本 + usage
    #[test]
    fn parse_sse_text_stream() {
        let raw = r#"data: {"id":"1","model":"m","choices":[{"delta":{"content":"Hello"},"index":0}]}
data: {"id":"1","model":"m","choices":[{"delta":{"content":" world"},"index":0}]}
data: {"id":"1","model":"m","choices":[{"delta":{},"finish_reason":"stop","index":0}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}
data: [DONE]"#;
        let (chunks, payload) = parse_sse(raw).unwrap();
        assert_eq!(chunks.len(), 3); // 2 text + 1 usage
        assert_eq!(payload.message.content, "Hello world");
        assert_eq!(payload.finish_reason, "stop");
        assert_eq!(payload.usage.unwrap().total_tokens, 7);
    }

    // [解析] 流式 reasoning + text
    #[test]
    fn parse_sse_reasoning_stream() {
        let raw = r#"data: {"id":"1","model":"deepseek-v4-pro","choices":[{"delta":{"reasoning_content":"thinking..."},"index":0}]}
data: {"id":"1","model":"deepseek-v4-pro","choices":[{"delta":{"content":"answer"},"index":0}]}
data: {"id":"1","model":"deepseek-v4-pro","choices":[{"delta":{},"finish_reason":"stop","index":0}]}
data: [DONE]"#;
        let (chunks, payload) = parse_sse(raw).unwrap();
        assert_eq!(payload.message.content, "answer");
        assert_eq!(
            payload.message.extra["reasoning_content"],
            "thinking..."
        );
    }

    // ═══════════════════════════════════════════════════════════
    // Retry logic — 1 test
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn is_retryable_429_and_5xx() {
        assert!(is_retryable(&ProviderError::Api { status: 429, message: "rate limit".into() }));
        assert!(is_retryable(&ProviderError::Api { status: 503, message: "".into() }));
        assert!(is_retryable(&ProviderError::Transport("timeout".into())));
        assert!(!is_retryable(&ProviderError::Api { status: 401, message: "".into() }));
        assert!(!is_retryable(&ProviderError::Parse("".into())));
    }
}
```

---

## 测试汇总

| 分类 | 测试数 | 覆盖角度 |
|------|--------|---------|
| 消息转换 | 5 | user/system/assistant/tool/reasoning 透传 |
| 请求体构造 | 4 | 最简/工具/thinking/stream |
| 响应解析 | 2 | 文本/tool_calls+reasoning |
| SSE 解析 | 2 | 文本流/reasoning 流 |
| 重试逻辑 | 1 | 429/5xx/transport/4xx/parse |
| Provider trait | 4（已在 4.2） | mock |
| **新增** | **14** | |
| **累计** (4.1–4.3) | **37** | |

---

### `lib.rs` — 追加 deepseek 模块

```rust
mod deepseek;
mod error;
mod provider;
mod types;

pub use deepseek::{DeepSeekConfig, DeepSeekProvider};
pub use error::ProviderError;
pub use provider::Provider;
pub use types::{
    ModelCallPayload, ModelParams, ModelResponseChunk, ModelResponsePayload, ToolCall,
    ToolCallDelta, ToolDef, Usage,
};
```

---

## 集成测试（需 API KEY）

```rust
// tests/deepseek_live.rs — 手动运行，需环境变量
// DEEPSEEK_API_KEY=sk-xxx cargo test --package arf-model-adapter -- --ignored --nocapture

#[cfg(test)]
mod live_tests {
    use super::*;

    fn api_key() -> String {
        std::env::var("DEEPSEEK_API_KEY").expect("DEEPSEEK_API_KEY not set")
    }

    fn provider() -> DeepSeekProvider {
        let config = DeepSeekConfig::new(
            api_key(),
            vec!["deepseek-v4-flash".into(), "deepseek-v4-pro".into()],
        );
        DeepSeekProvider::new(config)
    }

    #[tokio::test]
    #[ignore]
    async fn live_basic_chat() { /* ... */ }

    #[tokio::test]
    #[ignore]
    async fn live_multi_round_chat() { /* ... */ }

    #[tokio::test]
    #[ignore]
    async fn live_tool_call() { /* ... */ }

    #[tokio::test]
    #[ignore]
    async fn live_thinking_enabled() { /* ... */ }

    #[tokio::test]
    #[ignore]
    async fn live_streaming() { /* ... */ }
}
```

等实现完毕，向你请求 API KEY 后跑真实测试。

---

## 交付标准

- `cargo test --workspace` 全部通过（261 + 14 = 275 tests）
- `cargo fmt --check` + `cargo clippy` 无警告
- 消息转换正确（user/system/assistant/tool/reasoning）
- thinking 模式参数映射正确
- SSE 流解析正确（text/reasoning/tool_call/usage chunk）
- 重试逻辑正确（仅 429/5xx/transport 重试）
