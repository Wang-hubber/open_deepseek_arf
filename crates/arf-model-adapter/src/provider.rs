//! Provider trait — abstract a model provider's chat completion API.

use async_trait::async_trait;

use crate::types::{
    ModelParams, ModelResponseChunk, ModelResponsePayload, ToolDef,
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
