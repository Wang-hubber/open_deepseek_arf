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

/// Configuration for an OpenAI provider.
#[derive(Debug, Clone)]
pub struct OpenAIConfig {
    /// 完整请求 URL（含 path，无隐式拼接）。
    /// 例：`https://api.openai.com/v1/chat/completions` 或
    /// `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
    pub endpoint: String,
    /// API key (or placeholder for local LLMs).
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
            endpoint: "https://api.openai.com/v1/chat/completions".into(),
            api_key,
            models,
            timeout_secs: 320,
            max_retries: 3,
        }
    }
}

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

    fn endpoint(&self) -> &str {
        &self.config.endpoint
    }

    /// Single HTTP call.
    async fn send_request(&self, body: &Value) -> Result<String, ProviderError> {
        let response = self
            .client
            .post(self.endpoint())
            .header(
                "Authorization",
                format!("Bearer {}", self.config.api_key),
            )
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
}

// ── Message conversion ─────────────────────────────────────────────

/// Convert ARF ModelMessage to OpenAI API message format.
fn convert_message(msg: &ModelMessage) -> Value {
    let mut api_msg = serde_json::Map::new();
    api_msg.insert("role".into(), msg.role.clone().into());

    // Handle assistant with tool_calls in extra
    if msg.role == "assistant"
        && let Some(tc_list) = msg.extra.get("tool_calls").and_then(|v| v.as_array())
        && !tc_list.is_empty()
    {
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

    Value::Object(api_msg)
}

// ── Request body ────────────────────────────────────────────────────

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

    // Merge safe extra params (filter out DeepSeek-specific keys)
    if let Some(obj) = params.extra.as_object() {
        for (key, value) in obj {
            if key != "reasoning_effort" && key != "reasoning_content" {
                body.insert(key.clone(), value.clone());
            }
        }
    }

    Value::Object(body)
}

// ── Response parsing ────────────────────────────────────────────────

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

    let message = ModelMessage::new("assistant", content);

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

// ── Provider trait ──────────────────────────────────────────────────

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
        let raw = self.send_request(&body).await?;
        parse_response(&raw)
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
            .header(
                "Authorization",
                format!("Bearer {}", self.config.api_key),
            )
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

// ── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ═══════════════════════════════════════════════════════════
    // Message conversion — 4 tests
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
    }

    #[test]
    fn convert_assistant_message() {
        let msg = ModelMessage::new("assistant", "response");
        let api = convert_message(&msg);
        assert_eq!(api["role"], "assistant");
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

    // ═══════════════════════════════════════════════════════════
    // Request body — 3 tests
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn build_minimal_body() {
        let body = build_request_body(
            "gpt-4o",
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
        assert_eq!(body["model"], "gpt-4o");
        assert_eq!(body["stream"], false);
    }

    #[test]
    fn build_body_with_tools() {
        let tools = vec![ToolDef {
            name: "search".into(),
            description: "search web".into(),
            parameters: serde_json::json!({"type": "object"}),
        }];
        let body = build_request_body(
            "gpt-4o",
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
    }

    #[test]
    fn build_body_passes_extra_params() {
        let body = build_request_body(
            "gpt-4o",
            &[],
            &[],
            &ModelParams {
                temperature: Some(0.5),
                max_tokens: Some(1024),
                thinking_enabled: false,
                extra: serde_json::json!({"top_p": 0.9, "frequency_penalty": 0.5}),
            },
            false,
        );
        assert_eq!(body["temperature"], 0.5);
        assert_eq!(body["max_tokens"], 1024);
        assert_eq!(body["top_p"], 0.9);
        assert_eq!(body["frequency_penalty"], 0.5);
        // DeepSeek-specific keys are filtered
        assert!(body.get("reasoning_effort").is_none());
    }

    // ═══════════════════════════════════════════════════════════
    // Response parsing — 1 test
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn parse_text_response() {
        let raw = r#"{
            "id": "chatcmpl-123",
            "model": "gpt-4o",
            "choices": [{
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Hello!"}
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }"#;
        let result = parse_response(raw).unwrap();
        assert_eq!(result.message.content, "Hello!");
        assert_eq!(result.finish_reason, "stop");
        assert_eq!(result.usage.unwrap().total_tokens, 15);
    }
}
