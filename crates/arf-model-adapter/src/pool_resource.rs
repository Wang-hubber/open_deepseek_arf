//! ModelAdapterResource — `Resource` impl for ModelAdapter (Phase 6 task 6.17).
//!
//! Wraps a Provider (the underlying model API) as a pooled Resource so that
//! many concurrent calls can share rate-limited API connections.
//!
//! Note: Most Provider impls (DeepSeek, OpenAI, Anthropic) are stateless and
//! thread-safe; this is more about rate limiting / quota management than
//! connection pooling. Real production use would combine with a connection
//! pool (e.g., hyper Client).

use std::sync::Arc;

use arf_pool::Resource;

use crate::Provider;

/// A pooled ModelAdapter resource: holds an Arc<dyn Provider> + per-resource
/// rate-limit state.
pub struct ModelAdapterResource {
    provider: Arc<dyn Provider>,
    /// Per-resource call counter (resets on acquire).
    call_count: std::sync::atomic::AtomicU64,
    /// Per-resource last-used timestamp (millis since epoch).
    last_used_ms: std::sync::atomic::AtomicU64,
}

impl ModelAdapterResource {
    pub fn new(provider: Arc<dyn Provider>) -> Self {
        Self {
            provider,
            call_count: std::sync::atomic::AtomicU64::new(0),
            last_used_ms: std::sync::atomic::AtomicU64::new(0),
        }
    }

    pub fn provider(&self) -> &Arc<dyn Provider> {
        &self.provider
    }

    pub fn call_count(&self) -> u64 {
        self.call_count.load(std::sync::atomic::Ordering::Relaxed)
    }
}

impl Resource for ModelAdapterResource {
    fn kind(&self) -> &str {
        "model_adapter"
    }

    fn try_acquire(&self) -> Result<(), String> {
        // Reset per-lease counters; resource is always acquirable unless drained.
        self.call_count
            .store(0, std::sync::atomic::Ordering::Relaxed);
        self.last_used_ms.store(
            now_ms(),
            std::sync::atomic::Ordering::Relaxed,
        );
        Ok(())
    }

    fn release(&self) {
        // No-op: Provider is stateless; no cleanup needed.
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::provider::Provider;
    use crate::types::{ModelParams, ModelResponsePayload, Usage};
    use arf_core::ModelMessage;
    use async_trait::async_trait;

    struct StubProvider;
    #[async_trait]
    impl Provider for StubProvider {
        fn name(&self) -> &str {
            "stub"
        }
        fn supported_models(&self) -> &[String] {
            static MODELS: std::sync::LazyLock<Vec<String>> =
                std::sync::LazyLock::new(|| vec![String::from("stub-v1")]);
            &MODELS
        }
        async fn chat(
            &self,
            _model_name: &str,
            _messages: Vec<ModelMessage>,
            _tools: Vec<crate::types::ToolDef>,
            _params: ModelParams,
        ) -> Result<ModelResponsePayload, crate::ProviderError> {
            Ok(ModelResponsePayload {
                message: ModelMessage::new("assistant", "ok"),
                tool_calls: None,
                finish_reason: "stop".into(),
                usage: Some(Usage {
                    input_tokens: 0,
                    output_tokens: 0,
                    total_tokens: 0,
                }),
                id: "stub".into(),
                model: "stub-v1".into(),
            })
        }
    }

    #[test]
    fn resource_kind_and_provider() {
        let r = ModelAdapterResource::new(Arc::new(StubProvider));
        assert_eq!(r.kind(), "model_adapter");
        assert_eq!(r.provider().name(), "stub");
    }

    #[test]
    fn try_acquire_resets_counter() {
        let r = ModelAdapterResource::new(Arc::new(StubProvider));
        r.call_count.store(5, std::sync::atomic::Ordering::Relaxed);
        r.try_acquire().unwrap();
        assert_eq!(r.call_count(), 0);
    }
}