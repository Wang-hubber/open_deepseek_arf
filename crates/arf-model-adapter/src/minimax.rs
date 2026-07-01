//! MiniMax provider — OpenAI-compatible chat completions.
//!
//! Base URL: `https://api.minimaxi.com/v1`（`/v1` 在 base URL 内，
//! 与 OpenAI 的 `https://api.openai.com` 无 path 模式不同）。
//! Env var: `MINIMAX_API_KEY`（也可读 `MINIMAX_TOKEN` 兼容）。
//!
//! Used by Phase 6 E2E tests as the primary live provider.

use std::time::Duration;

use async_trait::async_trait;
use reqwest::Client;
use serde_json::Value;

use arf_core::ModelMessage;

use crate::convert;
use crate::error::ProviderError;
use crate::provider::Provider;
use crate::types::{
    ModelParams, ModelResponsePayload, ToolCall, ToolDef, Usage,
};

/// Configuration for the MiniMax provider.
#[derive(Debug, Clone)]
pub struct MiniMaxConfig {
    pub base_url: String,
    pub api_key: String,
    pub models: Vec<String>,
    pub timeout_secs: u64,
    pub max_retries: u32,
}

impl MiniMaxConfig {
    /// Default base URL `https://api.minimaxi.com/v1`, default model `MiniMax-M3`.
    pub fn default() -> Self {
        Self {
            base_url: "https://api.minimaxi.com/v1".into(),
            api_key: String::new(),
            models: vec!["MiniMax-M3".into()],
            timeout_secs: 320,
            max_retries: 3,
        }
    }

    /// Read `MINIMAX_API_KEY` (or `MINIMAX_TOKEN` fallback) from env, fill api_key.
    pub fn from_env() -> Result<Self, ProviderError> {
        let api_key = std::env::var("MINIMAX_API_KEY")
            .or_else(|_| std::env::var("MINIMAX_TOKEN"))
            .map_err(|_| ProviderError::Parse("MINIMAX_API_KEY not set".into()))?;
        let mut cfg = Self::default();
        cfg.api_key = api_key;
        Ok(cfg)
    }
}

/// MiniMax API provider — OpenAI-compatible chat completions.
pub struct MiniMaxProvider {
    config: MiniMaxConfig,
    client: Client,
}

impl MiniMaxProvider {
    pub fn new(config: MiniMaxConfig) -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(config.timeout_secs))
            .build()
            .expect("reqwest client should always build");
        Self { config, client }
    }

    /// base_url already contains /v1, so just append /chat/completions.
    fn endpoint(&self) -> String {
        format!("{}/chat/completions", self.config.base_url)
    }

    /// Convert ARF ModelMessage → MiniMax request body (OpenAI format).
    fn build_request_body(
        &self,
        model_name: &str,
        messages: &[ModelMessage],
        tools: &[ToolDef],
        params: &ModelParams,
    ) -> Value {
        let msgs: Vec<Value> = messages
            .iter()
            .map(|m| {
                let mut obj = serde_json::Map::new();
                obj.insert("role".into(), Value::String(m.role.clone()));
                obj.insert("content".into(), Value::String(m.content.clone()));
                if !m.tool_calls.is_empty() {
                    let tcs: Vec<Value> = m
                        .tool_calls
                        .iter()
                        .map(|tc| {
                            serde_json::json!({
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": serde_json::to_string(&tc.arguments)
                                        .unwrap_or_default(),
                                }
                            })
                        })
                        .collect();
                    obj.insert("tool_calls".into(), Value::Array(tcs));
                }
                if let Some(tc_id) = &m.tool_call_id {
                    obj.insert("tool_call_id".into(), Value::String(tc_id.clone()));
                }
                if let Some(name) = &m.name {
                    obj.insert("name".into(), Value::String(name.clone()));
                }
                Value::Object(obj)
            })
            .collect();

        let mut body = serde_json::json!({
            "model": model_name,
            "messages": msgs,
        });

        if let Some(t) = params.temperature {
            body["temperature"] = Value::from(t);
        }
        if let Some(mt) = params.max_tokens {
            body["max_tokens"] = Value::from(mt);
        }
        if !tools.is_empty() {
            let tool_arr: Vec<Value> = tools
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
                .collect();
            body["tools"] = Value::Array(tool_arr);
        }
        body
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

    fn parse_response(&self, raw: &str) -> Result<ModelResponsePayload, ProviderError> {
        let v: Value =
            serde_json::from_str(raw).map_err(|e| ProviderError::Parse(e.to_string()))?;

        let choice = v
            .get("choices")
            .and_then(|c| c.get(0))
            .ok_or_else(|| ProviderError::Parse("missing choices[0]".into()))?;
        let message = choice
            .get("message")
            .ok_or_else(|| ProviderError::Parse("missing message".into()))?;
        let content = message
            .get("content")
            .and_then(|c| c.as_str())
            .unwrap_or("")
            .to_string();
        let finish_reason = choice
            .get("finish_reason")
            .and_then(|f| f.as_str())
            .unwrap_or("stop")
            .to_string();
        let tool_calls: Vec<ToolCall> = message
            .get("tool_calls")
            .and_then(|tc| tc.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|t| {
                        let id = t.get("id")?.as_str()?.to_string();
                        let func = t.get("function")?;
                        let name = func.get("name")?.as_str()?.to_string();
                        let args_str = func.get("arguments")?.as_str()?;
                        let arguments = serde_json::from_str(args_str).unwrap_or(Value::Null);
                        Some(ToolCall {
                            id,
                            name,
                            arguments,
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        let usage = v.get("usage").map(|u| Usage {
            input_tokens: u.get("prompt_tokens").and_then(|t| t.as_u64()).unwrap_or(0) as u32,
            output_tokens: u.get("completion_tokens").and_then(|t| t.as_u64()).unwrap_or(0) as u32,
            total_tokens: u.get("total_tokens").and_then(|t| t.as_u64()).unwrap_or(0) as u32,
        });
        let id = v
            .get("id")
            .and_then(|i| i.as_str())
            .unwrap_or("")
            .to_string();
        let model = v
            .get("model")
            .and_then(|m| m.as_str())
            .unwrap_or("")
            .to_string();

        Ok(ModelResponsePayload {
            message: ModelMessage::new("assistant", content),
            tool_calls: if tool_calls.is_empty() { None } else { Some(tool_calls) },
            finish_reason,
            usage,
            id,
            model,
        })
    }

    async fn call_with_retry(
        &self,
        body: Value,
    ) -> Result<ModelResponsePayload, ProviderError> {
        let mut last_error = String::new();
        for attempt in 0..=self.config.max_retries {
            match self.send_request(&body).await {
                Ok(raw) => return self.parse_response(&raw),
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
}

#[async_trait]
impl Provider for MiniMaxProvider {
    fn name(&self) -> &str {
        "minimax"
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
        let body = self.build_request_body(model_name, &messages, &tools, &params);
        self.call_with_retry(body).await
    }
}

// ── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // Serialize env-mutating tests so they don't race each other in parallel runs.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn default_config_has_minimax_base_url() {
        let cfg = MiniMaxConfig::default();
        assert_eq!(cfg.base_url, "https://api.minimaxi.com/v1");
        assert!(cfg.models.contains(&"MiniMax-M3".to_string()));
        assert_eq!(cfg.timeout_secs, 320);
        assert_eq!(cfg.max_retries, 3);
    }

    #[test]
    fn endpoint_appends_chat_completions() {
        let cfg = MiniMaxConfig {
            api_key: "k".into(),
            ..MiniMaxConfig::default()
        };
        let p = MiniMaxProvider::new(cfg);
        assert_eq!(
            p.endpoint(),
            "https://api.minimaxi.com/v1/chat/completions"
        );
    }

    #[test]
    fn from_env_reads_minimax_api_key() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        unsafe {
            std::env::set_var("MINIMAX_API_KEY", "test-key");
            std::env::remove_var("MINIMAX_TOKEN");
        }
        let cfg = MiniMaxConfig::from_env().expect("should read env");
        assert_eq!(cfg.api_key, "test-key");
        assert_eq!(cfg.base_url, "https://api.minimaxi.com/v1");
        unsafe {
            std::env::remove_var("MINIMAX_API_KEY");
        }
    }

    #[test]
    fn from_env_falls_back_to_minimax_token() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        unsafe {
            std::env::remove_var("MINIMAX_API_KEY");
            std::env::set_var("MINIMAX_TOKEN", "fallback-key");
        }
        let cfg = MiniMaxConfig::from_env().expect("should read fallback env");
        assert_eq!(cfg.api_key, "fallback-key");
        unsafe {
            std::env::remove_var("MINIMAX_TOKEN");
        }
    }

    #[test]
    fn from_env_errors_when_neither_set() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        unsafe {
            std::env::remove_var("MINIMAX_API_KEY");
            std::env::remove_var("MINIMAX_TOKEN");
        }
        let result = MiniMaxConfig::from_env();
        assert!(result.is_err());
    }
}
