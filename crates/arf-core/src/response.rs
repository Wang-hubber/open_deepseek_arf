//! Response — single-variant protocol for Bus receivers.
//!
//! Phase 6 task 6.2: Engine protocol semantics.
//!
//! **Invariants** (Phase 6 §1.2 / §2.P5):
//! - Only `Done(Value)`: Engine doesn't dispatch error variants
//! - Errors flow through `node_offline` lifecycle + `OnMemberFailedHandler`
//! - Business errors (e.g. permission denied) ride inside `Value` and are
//!   interpreted by App's `ResponseProcessor`
//! - Engine has no `Wait` variant: slow consumer ≠ engine-pause

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Engine-facing response type.
///
/// `Done` carries the final value from a receiver. The internal schema of
/// `Value` depends on the response `msg_type`:
/// - `"model_response"` → `{content, tool_calls, usage}`
/// - `"tool_result"` → `{content, error?}`
/// - custom types → App-defined
///
/// Engine does NOT parse `Value` contents. Business errors (e.g.
/// `"permission denied"`) ride inside `Value` and are interpreted by App.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum Response {
    /// Final value from receiver.
    Done(Value),
}
