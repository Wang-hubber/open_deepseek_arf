//! Wire-level message-type constants (Phase 9 A3-001).
//!
//! A single source of truth for every message type that rides on the Bus.
//! Previously these were scattered string literals across `arf-bus`,
//! `arf-core`, `arf-engine`, and `arf-model-adapter`; renaming or typo'ing a
//! type silently broke routing.
//!
//! Rule: use these constants instead of bare `"model_call"` / `"node_online"`
//! etc. at every construction site.

pub const NODE_ONLINE: &str = "node_online";
pub const NODE_OFFLINE: &str = "node_offline";
pub const HEARTBEAT_REQUEST: &str = "heartbeat_request";
pub const HEARTBEAT_ACK: &str = "heartbeat_ack";
pub const BARRIER_REQUEST: &str = "barrier_request";
pub const BARRIER_ACK: &str = "barrier_ack";
pub const MODEL_CALL: &str = "model_call";
pub const MODEL_RESPONSE: &str = "model_response";
pub const MODEL_RESPONSE_CHUNK: &str = "model_response_chunk";
pub const TOOL_CALL: &str = "tool_call";
pub const TOOL_RESULT: &str = "tool_result";
pub const PERMISSION_REQUEST: &str = "permission_request";
pub const PERMISSION_RESPONSE: &str = "permission_response";
pub const PEER_MESSAGE: &str = "peer_message";
pub const PEER_REPLY: &str = "peer_reply";
/// `Engine → SubagentPool node`: a parent Engine delegates a one-shot
/// task to a pool-managed ephemeral Engine. The pool replies with
/// [`SUBAGENT_RESULT`] keyed by `correlation_id`.
pub const SUBAGENT_DELEGATE: &str = "subagent_delegate";
/// Pool's reply to a [`SUBAGENT_DELEGATE`] — `correlation_id` echoes
/// the request so the Engine's `WaitEvent` matches.
pub const SUBAGENT_RESULT: &str = "subagent_result";
pub const SESSION_SAVE: &str = "session_save";
pub const SESSION_SNAPSHOT: &str = "session_snapshot";