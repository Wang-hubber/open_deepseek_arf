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

use crate::error::ProviderError;
use crate::provider::Provider;
use crate::types::{
    ModelParams, ModelResponseChunk, ModelResponsePayload, ToolCall, ToolCallDelta, ToolDef,
    Usage,
};

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
                    let delay = 2u64.pow(attempt + 1);
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
        let text = response
            .text()
            .await
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

// ── Message conversion ─────────────────────────────────────────────

/// Convert ARF ModelMessage to DeepSeek API message format.
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

    // Passthrough reasoning_content for thinking continuity
    if !msg.extra.is_null() && msg.extra.get("reasoning_content").is_some() {
        api_msg.insert(
            "reasoning_content".into(),
            msg.extra["reasoning_content"].clone(),
        );
    }

    Value::Object(api_msg)
}

// ── Request body ────────────────────────────────────────────────────

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

    Value::Object(body)
}

// ── Response parsing ────────────────────────────────────────────────

/// Raw API response shape.
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
        && !rc.is_empty() {
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

// ── SSE parsing ─────────────────────────────────────────────────────

/// Parse SSE event stream into chunks + final response.
fn parse_sse(raw: &str) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
    let mut chunks = Vec::new();
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
            continue;
        }
        if let Some(data) = line.strip_prefix("data: ") {
            if data == "[DONE]" {
                break;
            }
            if let Ok(chunk) = serde_json::from_str::<Value>(data) {
                if let Some(m) = chunk.get("model").and_then(|v| v.as_str()) {
                    model = m.to_string();
                }
                if let Some(id) = chunk.get("id").and_then(|v| v.as_str()) {
                    response_id = id.to_string();
                }

                if let Some(choices) = chunk.get("choices").and_then(|v| v.as_array()) {
                    for choice in choices {
                        if let Some(fr) = choice.get("finish_reason").and_then(|v| v.as_str())
                            && !fr.is_empty() {
                                finish_reason = fr.to_string();
                            }

                        if let Some(delta) = choice.get("delta") {
                            // Text content
                            if let Some(c) = delta.get("content").and_then(|v| v.as_str())
                                && !c.is_empty() {
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
                            if let Some(rc) =
                                delta.get("reasoning_content").and_then(|v| v.as_str())
                                && !rc.is_empty() {
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
                            if let Some(tc_list) =
                                delta.get("tool_calls").and_then(|v| v.as_array())
                            {
                                for tc in tc_list {
                                    let index =
                                        tc.get("index").and_then(|v| v.as_u64()).unwrap_or(0)
                                            as u32;
                                    let tc_id = tc
                                        .get("id")
                                        .and_then(|v| v.as_str())
                                        .map(|s| s.to_string());
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
                                        tool_call: Some(ToolCallDelta {
                                            index,
                                            id: tc_id.clone(),
                                            name: tc_name.clone(),
                                            arguments_delta: args_delta.clone(),
                                        }),
                                        usage: None,
                                    });

                                    if let Some(ref id) = tc_id
                                        && let Some(ref name) = tc_name {
                                            if let Some(existing) = acc_tool_calls
                                                .iter_mut()
                                                .find(|tc| tc.id == *id)
                                            {
                                                if let Some(ref delta) = args_delta
                                                    && let Value::String(ref mut s) =
                                                        existing.arguments
                                                    {
                                                        s.push_str(delta);
                                                    }
                                            } else {
                                                let args_str = args_delta.unwrap_or_default();
                                                let args: Value = serde_json::from_str(&args_str)
                                                    .unwrap_or(Value::String(args_str));
                                                acc_tool_calls.push(ToolCall {
                                                    id: id.clone(),
                                                    name: name.clone(),
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

    let payload = ModelResponsePayload {
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
    };

    Ok((chunks, payload))
}

// ── Retry helpers ───────────────────────────────────────────────────

/// Determine if an error is retryable.
fn is_retryable(err: &ProviderError) -> bool {
    match err {
        ProviderError::Api { status, .. } => *status == 429 || (500..600).contains(status),
        ProviderError::Transport(_) => true,
        _ => false,
    }
}

// ── Tests ───────────────────────────────────────────────────────────

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
            "m",
            &[],
            &[],
            &ModelParams {
                temperature: None,
                max_tokens: None,
                thinking_enabled: false,
                extra: Value::Null,
            },
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
        assert_eq!(
            result.message.extra["reasoning_content"],
            "I need to search"
        );
    }

    // ═══════════════════════════════════════════════════════════
    // SSE parsing — 2 tests
    // ═══════════════════════════════════════════════════════════

    // [解析] 流式文本 + usage
    #[test]
    fn parse_sse_text_stream() {
        let raw = concat!(
            "data: {\"id\":\"1\",\"model\":\"m\",\"choices\":[{\"delta\":{\"content\":\"Hello\"},\"index\":0}]}\n",
            "data: {\"id\":\"1\",\"model\":\"m\",\"choices\":[{\"delta\":{\"content\":\" world\"},\"index\":0}]}\n",
            "data: {\"id\":\"1\",\"model\":\"m\",\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\",\"index\":0}],\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":2,\"total_tokens\":7}}\n",
            "data: [DONE]"
        );
        let (chunks, payload) = parse_sse(raw).unwrap();
        assert_eq!(chunks.len(), 3); // 2 text + 1 usage
        assert_eq!(payload.message.content, "Hello world");
        assert_eq!(payload.finish_reason, "stop");
        assert_eq!(payload.usage.unwrap().total_tokens, 7);
    }

    // [解析] 流式 reasoning + text
    #[test]
    fn parse_sse_reasoning_stream() {
        let raw = concat!(
            "data: {\"id\":\"1\",\"model\":\"deepseek-v4-pro\",\"choices\":[{\"delta\":{\"reasoning_content\":\"thinking...\"},\"index\":0}]}\n",
            "data: {\"id\":\"1\",\"model\":\"deepseek-v4-pro\",\"choices\":[{\"delta\":{\"content\":\"answer\"},\"index\":0}]}\n",
            "data: {\"id\":\"1\",\"model\":\"deepseek-v4-pro\",\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\",\"index\":0}]}\n",
            "data: [DONE]"
        );
        let (_chunks, payload) = parse_sse(raw).unwrap();
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
        assert!(is_retryable(&ProviderError::Api {
            status: 429,
            message: "rate limit".into()
        }));
        assert!(is_retryable(&ProviderError::Api {
            status: 503,
            message: "".into()
        }));
        assert!(is_retryable(&ProviderError::Transport("timeout".into())));
        assert!(!is_retryable(&ProviderError::Api {
            status: 401,
            message: "".into()
        }));
        assert!(!is_retryable(&ProviderError::Parse("".into())));
    }
}
