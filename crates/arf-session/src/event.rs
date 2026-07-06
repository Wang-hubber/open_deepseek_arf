//! Unified event log for SessionStore. Replaces ad-hoc per-event-type
//! record_* methods with a single tagged enum.
//!
//! Spec: docs/superpowers/specs/2026-07-06-unified-async-outbox-design.md §2

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

// === Event enum ============================================================

/// Append-only event written by Engine via `SessionStore::record_event`.
///
/// The `#[serde(tag = "kind")]` derives a JSON object like
/// `{"kind":"outbound_sent", "msg_type":"peer_message", ...}` so consumers
/// (JSONL readers, debug tools) can dispatch without knowing Rust types.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Event {
    /// Engine sent an async outbound message (peer_message, HumanHandoff, …)
    /// before `bus.send`. `attempt = 1` for the original send, +1 per resend.
    OutboundSent {
        msg_type: String,
        correlation_id: Uuid,
        attempt: u32,
        target: Vec<String>,           // NodeIds
        payload: Value,
        captured_at: DateTime<Utc>,
    },
    /// Engine received an inbound reply (peer_reply, human_handoff_reply, …).
    InboundReply {
        msg_type: String,
        correlation_id: Uuid,
        source: String,                // NodeId of the responder
        payload: Value,
        captured_at: DateTime<Utc>,
    },
    RoundStart {
        round: u32,
        captured_at: DateTime<Utc>,
    },
    RoundEnd {
        round: u32,
        captured_at: DateTime<Utc>,
    },
    ModelCallEnd {
        round: u32,
        turn: u32,
        model: String,
        input_tokens: u32,
        output_tokens: u32,
        total_tokens: u32,
        captured_at: DateTime<Utc>,
    },
    ToolCallEnd {
        round: u32,
        turn: u32,
        tool: String,
        success: bool,
        error: Option<String>,
        captured_at: DateTime<Utc>,
    },
}

/// One row of `pending_outbound`. Returned by
/// `SessionStore::pending_outbound(session_id)`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PendingOutbound {
    pub msg_type: String,
    pub correlation_id: Uuid,
    pub target_nodes: Vec<String>,
    pub payload: Value,
    pub attempt: u32,
}

// === Tests =================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // [序列化] 每个 Event 变体能 round-trip
    #[test]
    fn event_outbound_sent_roundtrip() {
        let evt = Event::OutboundSent {
            msg_type: "peer_message".into(),
            correlation_id: Uuid::nil(),
            attempt: 1,
            target: vec!["node-B".into()],
            payload: json!({"hello": "world"}),
            captured_at: Utc::now(),
        };
        let s = serde_json::to_string(&evt).unwrap();
        let back: Event = serde_json::from_str(&s).unwrap();
        assert_eq!(back, evt);
        assert!(s.contains("\"kind\":\"outbound_sent\""), "raw: {s}");
    }

    // [序列化]
    #[test]
    fn event_inbound_reply_roundtrip() {
        let evt = Event::InboundReply {
            msg_type: "human_handoff_reply".into(),
            correlation_id: Uuid::nil(),
            source: "ui".into(),
            payload: json!({"answer": "yes"}),
            captured_at: Utc::now(),
        };
        let s = serde_json::to_string(&evt).unwrap();
        let back: Event = serde_json::from_str(&s).unwrap();
        assert_eq!(back, evt);
        assert!(s.contains("\"kind\":\"inbound_reply\""));
    }

    // [序列化]
    #[test]
    fn event_round_start_roundtrip() {
        let evt = Event::RoundStart { round: 3, captured_at: Utc::now() };
        let s = serde_json::to_string(&evt).unwrap();
        let back: Event = serde_json::from_str(&s).unwrap();
        assert_eq!(back, evt);
    }

    // [序列化]
    #[test]
    fn event_round_end_roundtrip() {
        let evt = Event::RoundEnd { round: 5, captured_at: Utc::now() };
        let s = serde_json::to_string(&evt).unwrap();
        let back: Event = serde_json::from_str(&s).unwrap();
        assert_eq!(back, evt);
    }

    // [序列化]
    #[test]
    fn event_model_call_end_roundtrip() {
        let evt = Event::ModelCallEnd {
            round: 1, turn: 2, model: "deepseek-v4".into(),
            input_tokens: 100, output_tokens: 50, total_tokens: 150,
            captured_at: Utc::now(),
        };
        let s = serde_json::to_string(&evt).unwrap();
        let back: Event = serde_json::from_str(&s).unwrap();
        assert_eq!(back, evt);
    }

    // [序列化]
    #[test]
    fn event_tool_call_end_roundtrip() {
        let evt = Event::ToolCallEnd {
            round: 1, turn: 2, tool: "fs.read".into(),
            success: false, error: Some("permission denied".into()),
            captured_at: Utc::now(),
        };
        let s = serde_json::to_string(&evt).unwrap();
        let back: Event = serde_json::from_str(&s).unwrap();
        assert_eq!(back, evt);
    }

    // [序列化]
    #[test]
    fn pending_outbound_roundtrip() {
        let p = PendingOutbound {
            msg_type: "human_handoff".into(),
            correlation_id: Uuid::nil(),
            target_nodes: vec!["ui".into()],
            payload: json!({"question": "ok?"}),
            attempt: 2,
        };
        let s = serde_json::to_string(&p).unwrap();
        let back: PendingOutbound = serde_json::from_str(&s).unwrap();
        assert_eq!(back, p);
    }
}