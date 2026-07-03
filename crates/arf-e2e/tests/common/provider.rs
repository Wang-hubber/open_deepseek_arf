//! Provider factories — live (MiniMax / DeepSeek / OpenAI) and a local mock
//! used for offline E2E tests.
//!
//! Note: `MockProvider` is a LOCAL struct defined in this crate's common
//! module. `arf-model-adapter` does NOT export a public Mock (it has
//! internal test-only mocks), so E2E tests must define their own. The
//! behaviour is intentionally simple: return programmed responses in
//! order, looping the last response when exhausted.

use std::sync::Arc;

use arf_model_adapter::provider::Provider;
use arf_model_adapter::types::{
    ModelParams, ModelResponsePayload, ToolCall, ToolDef, Usage,
};
use arf_model_adapter::ProviderError;
use arf_core::ModelMessage;
use async_trait::async_trait;

use super::env;

// ═══════════════════════════════════════════════════════════════════════
// ScriptedProvider — local E2E mock
// ═══════════════════════════════════════════════════════════════════════

/// A scripted Provider that returns queued [`ModelResponsePayload`] values
/// in order. When the queue is exhausted, the last response is returned
/// repeatedly (useful for tests that issue more model_calls than the script).
///
/// This is the E2E equivalent of the inline mock responder used in
/// `crates/arf-engine/tests/integration.rs`, but it is a real
/// `Arc<dyn Provider>` plugged into a real `ModelAdapterNode` — so the
/// engine-to-bus-to-node-to-provider chain is fully exercised.
pub struct ScriptedProvider {
    name: String,
    models: Vec<String>,
    responses: std::sync::Mutex<Vec<ModelResponsePayload>>,
    cursor: std::sync::atomic::AtomicUsize,
}

impl ScriptedProvider {
    pub fn new(name: &str, model: &str, responses: Vec<ModelResponsePayload>) -> Self {
        Self {
            name: name.to_string(),
            models: vec![model.to_string()],
            responses: std::sync::Mutex::new(responses),
            cursor: std::sync::atomic::AtomicUsize::new(0),
        }
    }
}

#[async_trait]
impl Provider for ScriptedProvider {
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
        let queue = self.responses.lock().expect("poisoned").clone();
        if queue.is_empty() {
            return Ok(ModelResponsePayload {
                message: ModelMessage::new("assistant", ""),
                tool_calls: None,
                finish_reason: "stop".into(),
                usage: None,
                id: String::new(),
                model: self.models[0].clone(),
            });
        }
        let idx = self
            .cursor
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let payload = queue
            .get(idx)
            .or_else(|| queue.last())
            .cloned()
            .expect("non-empty queue");
        Ok(payload)
    }
}

/// Wrap a [`ScriptedProvider`] in an `Arc<dyn Provider>`.
pub fn scripted(responses: Vec<ModelResponsePayload>) -> Arc<dyn Provider> {
    Arc::new(ScriptedProvider::new("scripted", "scripted-v1", responses))
}

// ═══════════════════════════════════════════════════════════════════════
// Public response builders
// ═══════════════════════════════════════════════════════════════════════

/// Build a single text-only response.
pub fn text_response(content: &str) -> ModelResponsePayload {
    ModelResponsePayload {
        message: ModelMessage::new("assistant", content),
        tool_calls: None,
        finish_reason: "stop".into(),
        usage: Some(Usage {
            input_tokens: 50,
            output_tokens: 10,
            total_tokens: 60,
        }),
        id: format!("e2e-text-{}", uuid::Uuid::new_v4()),
        model: "scripted-v1".into(),
    }
}

/// Build a response with one tool call.
pub fn tool_call_response(name: &str, args: serde_json::Value) -> ModelResponsePayload {
    ModelResponsePayload {
        message: ModelMessage::new("assistant", ""),
        tool_calls: Some(vec![ToolCall {
            id: format!("call_{}", uuid::Uuid::new_v4()),
            name: name.to_string(),
            arguments: args,
        }]),
        finish_reason: "tool_calls".into(),
        usage: Some(Usage {
            input_tokens: 50,
            output_tokens: 8,
            total_tokens: 58,
        }),
        id: format!("e2e-tool-{}", uuid::Uuid::new_v4()),
        model: "scripted-v1".into(),
    }
}

/// Build a single-response mock for simple test cases.
pub fn simple_mock(content: &str) -> Arc<dyn Provider> {
    scripted(vec![text_response(content)])
}

// ═══════════════════════════════════════════════════════════════════════
// Live provider factories (used by tests that opt-in to live calls)
// ═══════════════════════════════════════════════════════════════════════

/// Build a live [`MiniMaxProvider`] from the `MINIMAX_API_KEY` env var.
/// Returns `None` (with a printed warning) if the env var is not set.
pub fn live_minimax() -> Option<Arc<dyn Provider>> {
    let key = match env::require_minimax_key() {
        Some(k) => k,
        None => {
            env::skip_message("MINIMAX_API_KEY");
            return None;
        }
    };
    let mut cfg = arf_model_adapter::MiniMaxConfig::default();
    cfg.api_key = key;
    let provider: Arc<dyn Provider> = Arc::new(arf_model_adapter::MiniMaxProvider::new(cfg));
    Some(provider)
}

/// Build a live OpenAI-compatible provider pointed at DashScope (qwen) from
/// the `DASHSCOPE_API_KEY` env var. Returns `None` (with a printed warning)
/// if the env var is not set. Phase 9 task 9.2.2 — first live-LLM probe.
pub fn live_qwen() -> Option<Arc<dyn Provider>> {
    use arf_model_adapter::{OpenAIConfig, OpenAIProvider};
    let key = match env::require_dashscope_key() {
        Some(k) => k,
        None => {
            env::skip_message("DASHSCOPE_API_KEY");
            return None;
        }
    };
    let cfg = OpenAIConfig {
        endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions".into(),
        api_key: key,
        models: vec!["qwen3.7-max-preview".into()],
        ..OpenAIConfig::default()
    };
    let provider: Arc<dyn Provider> = Arc::new(OpenAIProvider::new(cfg));
    Some(provider)
}
