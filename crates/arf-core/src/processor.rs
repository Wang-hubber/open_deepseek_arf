//! ResponseProcessor — turn raw Bus `Message` into typed `Response`.
//!
//! App code registers processors for custom msg_types in
//! `AgentConfig.processors`. Built-in msg_types (`"model_response"`,
//! `"tool_result"`) have implicit dispatch inside Engine (no processor
//! needed — Phase 6 §1.1 whitelist).
//!
//! Phase 6 task 6.2: type definition only. Engine-side dispatch table
//! (HashMap<String, Arc<dyn ResponseProcessor>>) is wired in 6.8
//! (EngineBuilder API).

use crate::{Message, Response};

/// Convert a raw `Message` (typically a response with `correlation_id`
/// matching a pending WaitEvent) into a typed `Response`.
///
/// App implements this for each custom msg_type they expect.
///
/// **Concurrency**: must be `Send + Sync` because Engine may invoke
/// from any tokio task.
pub trait ResponseProcessor: Send + Sync {
    /// Whether this processor handles the given msg_type.
    /// Allows AgentConfig to dispatch by msg_type without dynamic dispatch.
    fn handles(&self, msg_type: &str) -> bool;

    /// Process the message. Returns `Err(msg)` (or any error string) if
    /// processor can't handle it (lets caller fall through to next
    /// processor; in practice each msg_type maps to exactly one processor).
    fn process(&self, msg: &Message) -> Result<Response, String>;
}
