//! ARF ModelAdapter — translate ARF messages to provider APIs.
//!
//! ModelAdapter is a passive Bus node. It listens for `model_call` messages,
//! translates ARF-internal `ModelMessage` format to provider-specific API
//! format, calls the model via HTTP, and returns `model_response` to the Bus.
//!
//! Providers (DeepSeek, OpenAI, Anthropic) implement the `Provider` trait.
//! Multiple ModelAdapter nodes can run simultaneously, each serving a
//! different model/provider.

mod error;
mod provider;
mod types;

pub use error::ProviderError;
pub use provider::Provider;
pub use types::{
    ModelCallPayload, ModelParams, ModelResponseChunk, ModelResponsePayload, ToolCall,
    ToolCallDelta, ToolDef, Usage,
};
