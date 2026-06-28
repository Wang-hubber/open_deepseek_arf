//! DeepSeek provider — OpenAI-compatible chat completions API.
//!
//! Endpoint: `POST /chat/completions`
//! Auth: Bearer token
//! Features: thinking mode, reasoning_content passthrough, streaming
//!
//! Uses shared SSE parsing and retry logic from `convert.rs`.
//! DeepSeek-specific: thinking mode mapping, reasoning_content passthrough.

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

/// Configuration for a DeepSeek provider.
#[derive(Debug, Clone)]
pub struct DeepSeekConfig {
    pub base_url: String,
    pub api_key: String,
    pub models: Vec<String>,
    pub timeout_secs: u64,
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
    pub fn new(config: DeepSeekConfig) -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(config.timeout_secs))
            .build()
            .expect("reqwest client should always build");
        Self { config, client }
    }

    fn endpoint(&self) -> String {
        format!("{}/chat/completions", self.config.base_url)
    }

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

    /// Single HTTP call with retry on 429/5xx.
    async fn call_with_retry(
        &self,
        body: Value,
    ) -> Result<ModelResponsePayload, ProviderError> {
        let mut last_error = String::new();
        for attempt in 0..=self.config.max_retries {
            match self.send_request(&body).await {
                Ok(raw) => return parse_response(&raw),
                Err(e) => {
                    last_error = e.to_string();
                    if !convert::is_retryable(&e) || attempt == self.config.max_retries {
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

        convert::parse_sse(&full_text)
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

// ── Message conversion (DeepSeek-specific: reasoning_content passthrough) ──

fn convert_message(msg: &ModelMessage) -> Value {
    let mut api_msg = serde_json::Map::new();
    api_msg.insert("role".into(), msg.role.clone().into());

    // Handle assistant with tool_calls in extra
    // (Phase 5 will add native tool_calls field to ModelMessage)
    if msg.role == "assistant"
        && let Some(tc_list) = msg.extra.get("tool_calls").and_then(|v| v.as_array())
        && !tc_list.is_empty()
    {
        // content is null when tool_calls are present
        api_msg.insert("content".into(), Value::Null);
        api_msg.insert("tool_calls".into(), Value::Array(tc_list.clone()));
    } else {
        api_msg.insert("content".into(), msg.content.clone().into());
    }

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

// ── Request body (DeepSeek-specific: thinking mode) ──────────────────

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

    // Thinking mode: ARF thinking_enabled → DeepSeek thinking + reasoning_effort
    // Docs: https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
    // - thinking: {type: "enabled"/"disabled"} — explicit switch
    // - reasoning_effort: "high"/"max" — top-level param, NOT inside thinking object
    body.insert(
        "thinking".into(),
        serde_json::json!({"type": if params.thinking_enabled { "enabled" } else { "disabled" }}),
    );
    if params.thinking_enabled {
        if let Some(effort) = params.extra.get("reasoning_effort").and_then(|v| v.as_str()) {
            body.insert("reasoning_effort".into(), effort.into());
        }
    }

    Value::Object(body)
}

// ── Response parsing (DeepSeek-specific: reasoning_content extraction) ──

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

// ── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::ToolDef;

    // ═══════════════════════════════════════════════════════════
    // Message conversion — 5 tests
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn convert_user_message() {
        let msg = ModelMessage::new("user", "hello");
        let api = convert_message(&msg);
        assert_eq!(api["role"], "user");
        assert_eq!(api["content"], "hello");
    }

    #[test]
    fn convert_system_message() {
        let msg = ModelMessage::new("system", "you are helpful");
        let api = convert_message(&msg);
        assert_eq!(api["role"], "system");
        assert_eq!(api["content"], "you are helpful");
    }

    #[test]
    fn convert_assistant_message() {
        let msg = ModelMessage::new("assistant", "response");
        let api = convert_message(&msg);
        assert_eq!(api["role"], "assistant");
        assert_eq!(api["content"], "response");
    }

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
        // reasoning_effort is top-level, not inside thinking
        assert_eq!(body["reasoning_effort"], "high");
    }

    #[test]
    fn build_body_thinking_disabled() {
        let body = build_request_body(
            "deepseek-v4-flash",
            &[],
            &[],
            &ModelParams {
                temperature: None,
                max_tokens: None,
                thinking_enabled: false,
                extra: Value::Null,
            },
            false,
        );
        let thinking = &body["thinking"];
        assert_eq!(thinking["type"], "disabled");
    }

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
}
