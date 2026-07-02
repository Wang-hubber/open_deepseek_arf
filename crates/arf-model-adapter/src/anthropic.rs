//! Anthropic provider — Messages API.
//!
//! Endpoint: `POST /v1/messages`
//! Auth: x-api-key header
//! Key differences from OpenAI: system top-level param, content blocks,
//! stop_reason field name, max_tokens required, different SSE format.

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

/// Configuration for an Anthropic provider.
#[derive(Debug, Clone)]
pub struct AnthropicConfig {
    /// 完整请求 URL（含 path，无隐式拼接）。
    /// 例：`https://api.anthropic.com/v1/messages` 或
    /// DeepSeek anthropic 兼容端点：`https://api.deepseek.com/anthropic`
    pub endpoint: String,
    /// API key.
    pub api_key: String,
    /// Supported models (e.g., ["claude-sonnet-4-6", "claude-opus-4-7"]).
    pub models: Vec<String>,
    pub timeout_secs: u64,
    pub max_retries: u32,
}

impl AnthropicConfig {
    pub fn new(api_key: String, models: Vec<String>) -> Self {
        Self {
            endpoint: "https://api.anthropic.com/v1/messages".into(),
            api_key,
            models,
            timeout_secs: 320,
            max_retries: 3,
        }
    }
}

/// Anthropic API provider — Messages API.
pub struct AnthropicProvider {
    config: AnthropicConfig,
    client: Client,
}

impl AnthropicProvider {
    pub fn new(config: AnthropicConfig) -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(config.timeout_secs))
            .build()
            .expect("reqwest client should always build");
        Self { config, client }
    }

    fn endpoint(&self) -> &str {
        &self.config.endpoint
    }

    async fn send_request(&self, body: &Value) -> Result<String, ProviderError> {
        let response = self
            .client
            .post(self.endpoint())
            .header("x-api-key", &self.config.api_key)
            .header("anthropic-version", "2023-06-01")
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
            .header("x-api-key", &self.config.api_key)
            .header("anthropic-version", "2023-06-01")
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

        parse_anthropic_sse(&full_text)
    }
}

#[async_trait]
impl Provider for AnthropicProvider {
    fn name(&self) -> &str {
        "anthropic"
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

/// Build Anthropic content blocks for a user or assistant message.
fn text_content_block(text: &str) -> Value {
    serde_json::json!([{"type": "text", "text": text}])
}

/// Convert ARF messages to Anthropic format.
/// Returns (system_prompt, api_messages).
fn convert_messages(messages: &[ModelMessage]) -> (Option<String>, Vec<Value>) {
    let mut system: Option<String> = None;
    let mut api_msgs: Vec<Value> = Vec::new();

    for msg in messages {
        match msg.role.as_str() {
            "system" => {
                if system.is_none() {
                    // First system message → top-level system param
                    system = Some(msg.content.clone());
                } else {
                    // Subsequent system messages → user message with system wrapper
                    api_msgs.push(serde_json::json!({
                        "role": "user",
                        "content": [{"type": "text", "text": format!("<system>{}</system>", msg.content)}]
                    }));
                }
            }
            "user" => {
                api_msgs.push(serde_json::json!({
                    "role": "user",
                    "content": text_content_block(&msg.content)
                }));
            }
            "assistant" => {
                // Check if this message has tool_calls in extra
                // (Phase 5 will add proper tool_calls field to ModelMessage)
                if let Some(tc_list) = msg.extra.get("tool_calls").and_then(|v| v.as_array()) {
                    let mut blocks: Vec<Value> = Vec::new();
                    if !msg.content.is_empty() {
                        blocks.push(serde_json::json!({"type": "text", "text": msg.content}));
                    }
                    for tc in tc_list {
                        blocks.push(serde_json::json!({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": tc["arguments"]
                        }));
                    }
                    api_msgs.push(serde_json::json!({
                        "role": "assistant",
                        "content": blocks
                    }));
                } else {
                    api_msgs.push(serde_json::json!({
                        "role": "assistant",
                        "content": text_content_block(&msg.content)
                    }));
                }
            }
            "tool" => {
                // Tool results → user role with tool_result content
                let tc_id = msg.tool_call_id.clone().unwrap_or_default();
                api_msgs.push(serde_json::json!({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tc_id,
                        "content": msg.content
                    }]
                }));
            }
            _ => {
                // Unknown role → passthrough as user
                api_msgs.push(serde_json::json!({
                    "role": "user",
                    "content": text_content_block(&msg.content)
                }));
            }
        }
    }

    (system, api_msgs)
}

// ── Request body ────────────────────────────────────────────────────

fn build_request_body(
    model_name: &str,
    messages: &[ModelMessage],
    tools: &[ToolDef],
    params: &ModelParams,
    stream: bool,
) -> Value {
    let (system, api_messages) = convert_messages(messages);

    let mut body = serde_json::Map::new();
    body.insert("model".into(), model_name.into());
    // Anthropic requires max_tokens
    body.insert(
        "max_tokens".into(),
        params.max_tokens.unwrap_or(4096).into(),
    );
    body.insert("messages".into(), api_messages.into());
    body.insert("stream".into(), stream.into());

    if let Some(sys) = system {
        body.insert("system".into(), sys.into());
    }
    if let Some(t) = params.temperature {
        body.insert("temperature".into(), t.into());
    }

    if !tools.is_empty() {
        body.insert(
            "tools".into(),
            tools
                .iter()
                .map(|t| {
                    serde_json::json!({
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.parameters,
                    })
                })
                .collect(),
        );
    }

    Value::Object(body)
}

// ── Response parsing ────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct AnthropicResponse {
    id: String,
    model: String,
    content: Vec<ContentBlock>,
    stop_reason: Option<String>,
    usage: Option<AnthropicUsage>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type")]
enum ContentBlock {
    #[serde(rename = "text")]
    Text { text: String },
    #[serde(rename = "tool_use")]
    ToolUse {
        id: String,
        name: String,
        input: Value,
    },
    /// DeepSeek thinking mode in Anthropic format
    #[serde(rename = "thinking")]
    Thinking {
        thinking: String,
    },
    #[serde(other)]
    Unknown,
}

#[derive(Debug, Deserialize)]
struct AnthropicUsage {
    input_tokens: u32,
    output_tokens: u32,
}

/// Map Anthropic stop_reason to ARF finish_reason.
fn map_stop_reason(stop: &str) -> &str {
    match stop {
        "end_turn" => "stop",
        "max_tokens" => "length",
        "stop_sequence" => "stop",
        "tool_use" => "tool_calls",
        other => other,
    }
}

fn parse_response(raw: &str) -> Result<ModelResponsePayload, ProviderError> {
    let api: AnthropicResponse =
        serde_json::from_str(raw).map_err(|e| ProviderError::Parse(e.to_string()))?;

    let mut text_parts = Vec::new();
    let mut tool_calls = Vec::new();
    let mut reasoning = String::new();

    for block in &api.content {
        match block {
            ContentBlock::Text { text } => text_parts.push(text.clone()),
            ContentBlock::ToolUse { id, name, input } => {
                tool_calls.push(ToolCall {
                    id: id.clone(),
                    name: name.clone(),
                    arguments: input.clone(),
                });
            }
            ContentBlock::Thinking { thinking: th } => {
                reasoning = th.clone();
            }
            ContentBlock::Unknown => {}
        }
    }

    let content = text_parts.join("");
    let mut extra = Value::Null;
    if !reasoning.is_empty() {
        extra = serde_json::json!({"reasoning_content": reasoning});
    }
    let message = ModelMessage::new("assistant", content).with_extra(extra);

    let finish_reason = api
        .stop_reason
        .as_deref()
        .map(map_stop_reason)
        .unwrap_or("unknown")
        .to_string();

    let tool_calls = if tool_calls.is_empty() {
        None
    } else {
        Some(tool_calls)
    };

    let usage = api.usage.map(|u| Usage {
        input_tokens: u.input_tokens,
        output_tokens: u.output_tokens,
        total_tokens: u.input_tokens + u.output_tokens,
    });

    Ok(ModelResponsePayload {
        message,
        tool_calls,
        finish_reason,
        usage,
        id: api.id,
        model: api.model,
    })
}

// ── SSE parsing (Anthropic format) ──────────────────────────────────

/// Parse Anthropic SSE event stream.
///
/// Anthropic SSE uses event+data lines (unlike OpenAI's data-only format).
/// Events: message_start, content_block_start, content_block_delta,
/// content_block_stop, message_delta, message_stop.
fn parse_anthropic_sse(
    raw: &str,
) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
    let mut chunks = Vec::new();
    let mut full_content = String::new();
    let mut tool_calls: Vec<ToolCall> = Vec::new();
    let mut finish_reason = String::new();
    let mut model = String::new();
    let mut response_id = String::new();
    let mut usage: Option<Usage> = None;

    let mut current_event = String::new();
    let mut current_data = String::new();

    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() {
            // Empty line = end of event, process it
            if !current_data.is_empty() {
                process_anthropic_event(
                    &current_event,
                    &current_data,
                    &mut chunks,
                    &mut full_content,
                    &mut tool_calls,
                    &mut finish_reason,
                    &mut model,
                    &mut response_id,
                    &mut usage,
                );
            }
            current_event.clear();
            current_data.clear();
            continue;
        }
        if let Some(event) = line.strip_prefix("event: ") {
            current_event = event.to_string();
        } else if let Some(data) = line.strip_prefix("data: ") {
            current_data = data.to_string();
        }
    }
    // Process last event if any
    if !current_data.is_empty() {
        process_anthropic_event(
            &current_event,
            &current_data,
            &mut chunks,
            &mut full_content,
            &mut tool_calls,
            &mut finish_reason,
            &mut model,
            &mut response_id,
            &mut usage,
        );
    }

    let message = ModelMessage::new("assistant", full_content);

    let tool_calls = if tool_calls.is_empty() {
        None
    } else {
        Some(tool_calls)
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

#[allow(clippy::too_many_arguments)]
fn process_anthropic_event(
    event: &str,
    data: &str,
    chunks: &mut Vec<ModelResponseChunk>,
    full_content: &mut String,
    tool_calls: &mut Vec<ToolCall>,
    finish_reason: &mut String,
    model: &mut String,
    response_id: &mut String,
    usage: &mut Option<Usage>,
) {
    if let Ok(json) = serde_json::from_str::<Value>(data) {
        match event {
            "message_start" => {
                if let Some(m) = json.get("message").and_then(|v| v.get("model")).and_then(|v| v.as_str()) {
                    *model = m.to_string();
                }
                if let Some(id) = json.get("message").and_then(|v| v.get("id")).and_then(|v| v.as_str()) {
                    *response_id = id.to_string();
                }
                if let Some(u) = json.get("message").and_then(|v| v.get("usage")) {
                    *usage = Some(Usage {
                        input_tokens: u["input_tokens"].as_u64().unwrap_or(0) as u32,
                        output_tokens: u["output_tokens"].as_u64().unwrap_or(0) as u32,
                        total_tokens: u["input_tokens"].as_u64().unwrap_or(0) as u32
                            + u["output_tokens"].as_u64().unwrap_or(0) as u32,
                    });
                }
            }
            "content_block_delta" => {
                if let Some(delta) = json.get("delta")
                    && let Some(text) = delta.get("text").and_then(|v| v.as_str()) {
                        full_content.push_str(text);
                        chunks.push(ModelResponseChunk {
                            chunk_type: "text".into(),
                            content: Some(text.to_string()),
                            reasoning: None,
                            tool_call: None,
                            usage: None,
                        });
                    }
            }
            "content_block_start" => {
                if let Some(block) = json.get("content_block")
                    && block.get("type").and_then(|v| v.as_str()) == Some("tool_use")
                        && let (Some(id), Some(name)) = (
                            block.get("id").and_then(|v| v.as_str()),
                            block.get("name").and_then(|v| v.as_str()),
                        ) {
                            let input = block.get("input").cloned().unwrap_or(Value::Null);
                            tool_calls.push(ToolCall {
                                id: id.to_string(),
                                name: name.to_string(),
                                arguments: input,
                            });
                            chunks.push(ModelResponseChunk {
                                chunk_type: "tool_call".into(),
                                content: None,
                                reasoning: None,
                                tool_call: Some(crate::types::ToolCallDelta {
                                    index: tool_calls.len() as u32 - 1,
                                    id: Some(id.to_string()),
                                    name: Some(name.to_string()),
                                    arguments_delta: None,
                                }),
                                usage: None,
                            });
                        }
            }
            "message_delta" => {
                if let Some(sr) = json.get("delta").and_then(|v| v.get("stop_reason")).and_then(|v| v.as_str()) {
                    *finish_reason = map_stop_reason(sr).to_string();
                }
                if let Some(u) = json.get("usage") {
                    *usage = Some(Usage {
                        input_tokens: u["input_tokens"].as_u64().unwrap_or(0) as u32,
                        output_tokens: u["output_tokens"].as_u64().unwrap_or(0) as u32,
                        total_tokens: u["input_tokens"].as_u64().unwrap_or(0) as u32
                            + u["output_tokens"].as_u64().unwrap_or(0) as u32,
                    });
                }
            }
            _ => {} // message_stop, ping — ignore
        }
    }
}

// ── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ═══════════════════════════════════════════════════════════
    // Message conversion — 6 tests
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn convert_user_message() {
        let msgs = vec![ModelMessage::new("user", "hello")];
        let (system, api) = convert_messages(&msgs);
        assert!(system.is_none());
        assert_eq!(api.len(), 1);
        assert_eq!(api[0]["role"], "user");
        assert_eq!(api[0]["content"][0]["type"], "text");
        assert_eq!(api[0]["content"][0]["text"], "hello");
    }

    #[test]
    fn convert_system_extracted() {
        let msgs = vec![
            ModelMessage::new("system", "you are helpful"),
            ModelMessage::new("user", "hi"),
        ];
        let (system, api) = convert_messages(&msgs);
        assert_eq!(system.unwrap(), "you are helpful");
        assert_eq!(api.len(), 1);
        assert_eq!(api[0]["role"], "user");
    }

    #[test]
    fn convert_assistant_message() {
        let msgs = vec![ModelMessage::new("assistant", "response")];
        let (_system, api) = convert_messages(&msgs);
        assert_eq!(api[0]["role"], "assistant");
        assert_eq!(api[0]["content"][0]["text"], "response");
    }

    #[test]
    fn convert_tool_message_to_user_role() {
        let msgs = vec![ModelMessage::new("tool", "result data")
            .with_tool_call_id("toolu_001")];
        let (_system, api) = convert_messages(&msgs);
        assert_eq!(api[0]["role"], "user");
        assert_eq!(api[0]["content"][0]["type"], "tool_result");
        assert_eq!(api[0]["content"][0]["tool_use_id"], "toolu_001");
        assert_eq!(api[0]["content"][0]["content"], "result data");
    }

    #[test]
    fn convert_assistant_with_tool_uses() {
        let msg = ModelMessage::new("assistant", "")
            .with_extra(serde_json::json!({
                "tool_calls": [{"id": "call_1", "name": "search", "arguments": {"q": "rust"}}]
            }));
        let (_system, api) = convert_messages(&[msg]);
        assert_eq!(api[0]["role"], "assistant");
        let blocks = api[0]["content"].as_array().unwrap();
        assert_eq!(blocks[0]["type"], "tool_use");
        assert_eq!(blocks[0]["name"], "search");
        assert_eq!(blocks[0]["input"]["q"], "rust");
    }

    #[test]
    fn convert_multiple_system_messages() {
        let msgs = vec![
            ModelMessage::new("system", "primary"),
            ModelMessage::new("system", "secondary"),
        ];
        let (system, api) = convert_messages(&msgs);
        assert_eq!(system.unwrap(), "primary");
        assert_eq!(api.len(), 1);
        // second system becomes wrapped user message
        assert_eq!(api[0]["role"], "user");
        assert!(api[0]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("secondary"));
    }

    // ═══════════════════════════════════════════════════════════
    // Request body — 3 tests
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn build_minimal_body() {
        let body = build_request_body(
            "claude-sonnet-4-6",
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
        assert_eq!(body["model"], "claude-sonnet-4-6");
        // max_tokens defaults to 4096
        assert_eq!(body["max_tokens"], 4096);
        assert_eq!(body["stream"], false);
    }

    #[test]
    fn build_body_with_system_and_tools() {
        let msgs = vec![
            ModelMessage::new("system", "you are helpful"),
            ModelMessage::new("user", "search rust"),
        ];
        let tools = vec![ToolDef {
            name: "search".into(),
            description: "search web".into(),
            parameters: serde_json::json!({"type": "object", "properties": {"q": {"type": "string"}}}),
        }];
        let body = build_request_body(
            "claude-sonnet-4-6",
            &msgs,
            &tools,
            &ModelParams {
                temperature: Some(0.5),
                max_tokens: Some(2048),
                thinking_enabled: false,
                extra: Value::Null,
            },
            true,
        );
        assert_eq!(body["system"], "you are helpful");
        assert_eq!(body["max_tokens"], 2048);
        assert_eq!(body["temperature"], 0.5);
        let api_tools = body["tools"].as_array().unwrap();
        assert_eq!(api_tools[0]["name"], "search");
        assert_eq!(api_tools[0]["input_schema"]["type"], "object"); // Anthropic uses input_schema
    }

    #[test]
    fn build_body_max_tokens_required() {
        let body = build_request_body(
            "m", &[], &[],
            &ModelParams {
                temperature: None, max_tokens: None, thinking_enabled: false, extra: Value::Null,
            },
            false,
        );
        // Anthropic requires max_tokens — defaults to 4096 if not set
        assert_eq!(body["max_tokens"], 4096);
    }

    // ═══════════════════════════════════════════════════════════
    // Response parsing — 2 tests
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn parse_text_response() {
        let raw = r#"{
            "id": "msg_123",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "Hello!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5}
        }"#;
        let result = parse_response(raw).unwrap();
        assert_eq!(result.message.content, "Hello!");
        assert_eq!(result.finish_reason, "stop"); // mapped from end_turn
        assert!(result.tool_calls.is_none());
        let u = result.usage.unwrap();
        assert_eq!(u.input_tokens, 10);
        assert_eq!(u.output_tokens, 5);
        assert_eq!(u.total_tokens, 15); // computed
    }

    #[test]
    fn parse_tool_use_response() {
        let raw = r#"{
            "id": "msg_456",
            "model": "claude-opus-4-7",
            "content": [
                {"type": "text", "text": "Let me search for that."},
                {"type": "tool_use", "id": "toolu_001", "name": "search", "input": {"q": "rust"}}
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 20, "output_tokens": 30}
        }"#;
        let result = parse_response(raw).unwrap();
        assert_eq!(result.message.content, "Let me search for that.");
        assert_eq!(result.finish_reason, "tool_calls"); // mapped from tool_use
        let tc = result.tool_calls.unwrap();
        assert_eq!(tc.len(), 1);
        assert_eq!(tc[0].name, "search");
        assert_eq!(tc[0].arguments["q"], "rust");
    }

    // ═══════════════════════════════════════════════════════════
    // Stop reason mapping — 1 test
    // ═══════════════════════════════════════════════════════════

    #[test]
    fn map_all_stop_reasons() {
        assert_eq!(map_stop_reason("end_turn"), "stop");
        assert_eq!(map_stop_reason("max_tokens"), "length");
        assert_eq!(map_stop_reason("stop_sequence"), "stop");
        assert_eq!(map_stop_reason("tool_use"), "tool_calls");
        assert_eq!(map_stop_reason("unknown_reason"), "unknown_reason");
    }
}
