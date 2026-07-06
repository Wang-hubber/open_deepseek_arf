//! Reconstruct a `Message` from a `PendingOutbound` row, dispatched on
//! `msg_type`. Used by `Engine::resend_pending_outbound` to recover after
//! a crash that interrupted the original send.

use arf_core::{HumanHandoff, Message, NodeId, PeerMessage};
use arf_session::PendingOutbound;
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum ReconstructError {
    #[error("unknown msg_type `{0}`")]
    UnknownMsgType(String),
    #[error("invalid msg payload for `{0}`: {1}")]
    InvalidPayload(String, serde_json::Error),
    #[error("missing field `{1}` in payload for `{0}`")]
    MissingField(String, &'static str),
}

/// Build a `Message` from a `PendingOutbound` row. `from` is the sender NodeId
/// (typically `Engine::agent_id`).
pub fn reconstruct_message(
    p: &PendingOutbound,
    from: NodeId,
) -> Result<Message, ReconstructError> {
    let to: Vec<NodeId> = p.target_nodes.iter().map(|n| NodeId::new(n.clone())).collect();
    match p.msg_type.as_str() {
        "peer_message" => {
            // PeerMessage.payload() is serde_json::to_value(PeerMessage { correlation_id, from_session, to_session, content, attachments })
            let from_session = p.payload.get("from_session")
                .and_then(|v| v.as_str())
                .ok_or_else(|| ReconstructError::MissingField("peer_message".to_string(), "from_session"))?
                .to_string();
            let to_session = p.payload.get("to_session")
                .and_then(|v| v.as_str())
                .ok_or_else(|| ReconstructError::MissingField("peer_message".to_string(), "to_session"))?
                .to_string();
            let content = p.payload.get("content")
                .and_then(|v| v.as_str())
                .ok_or_else(|| ReconstructError::MissingField("peer_message".to_string(), "content"))?
                .to_string();
            let peer = PeerMessage {
                correlation_id: p.correlation_id,
                from_session,
                to_session,
                content,
                attachments: p.payload.get("attachments")
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default(),
            };
            Ok(Message::new(
                String::from("peer_message"),
                from,
                to,
                serde_json::to_value(&peer).unwrap_or(serde_json::Value::Null),
            ))
        }
        "human_handoff" => {
            // HumanHandoff.payload() is serde_json::to_value(HumanHandoff { correlation_id, question, context, options })
            let question = p.payload.get("question")
                .and_then(|v| v.as_str())
                .ok_or_else(|| ReconstructError::MissingField("human_handoff".to_string(), "question"))?
                .to_string();
            let context = p.payload.get("context").cloned().unwrap_or(serde_json::Value::Null);
            let options: Vec<String> = p.payload.get("options")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().filter_map(|x| x.as_str().map(String::from)).collect())
                .unwrap_or_default();
            let handoff = HumanHandoff {
                correlation_id: p.correlation_id,
                question,
                context,
                options,
            };
            Ok(Message::new(
                String::from("human_handoff"),
                from,
                to,
                serde_json::to_value(&handoff).unwrap_or(serde_json::Value::Null),
            ))
        }
        // Future msg_types (cancel, retry, escalation, …) add a branch here.
        other => Err(ReconstructError::UnknownMsgType(other.to_string())),
    }
}

// === Tests =================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use uuid::Uuid;

    // [方法] peer_message round-trip
    #[test]
    fn reconstruct_peer_message() {
        let cid = Uuid::new_v4();
        // Replicate the exact payload shape from PeerMessage.payload()
        let payload = serde_json::to_value(&PeerMessage {
            correlation_id: cid,
            from_session: "A".into(),
            to_session: "B".into(),
            content: "hi".into(),
            attachments: vec![],
        }).unwrap();
        let p = PendingOutbound {
            msg_type: "peer_message".into(),
            correlation_id: cid,
            target_nodes: vec!["engine-B".into()],
            payload,
            attempt: 1,
        };
        let msg = reconstruct_message(&p, NodeId::new("engine-A")).unwrap();
        assert_eq!(msg.msg_type, "peer_message");
        assert_eq!(msg.correlation_id(), Some(cid));
        assert_eq!(msg.payload.get("content").and_then(|v| v.as_str()), Some("hi"));
    }

    // [方法] human_handoff round-trip
    #[test]
    fn reconstruct_human_handoff() {
        let cid = Uuid::new_v4();
        let payload = serde_json::to_value(&HumanHandoff {
            correlation_id: cid,
            question: "ok?".into(),
            context: json!({"x": 1}),
            options: vec!["yes".into(), "no".into()],
        }).unwrap();
        let p = PendingOutbound {
            msg_type: "human_handoff".into(),
            correlation_id: cid,
            target_nodes: vec!["ui".into()],
            payload,
            attempt: 2,
        };
        let msg = reconstruct_message(&p, NodeId::new("engine-A")).unwrap();
        assert_eq!(msg.msg_type, "human_handoff");
        assert_eq!(msg.correlation_id(), Some(cid));
        assert_eq!(msg.payload.get("question").and_then(|v| v.as_str()), Some("ok?"));
    }

    // [边界] 未知 msg_type 返回 Err
    #[test]
    fn reconstruct_unknown_msg_type_errs() {
        let p = PendingOutbound {
            msg_type: "future_msg".into(),
            correlation_id: Uuid::new_v4(),
            target_nodes: vec!["x".into()],
            payload: json!({}),
            attempt: 1,
        };
        let err = reconstruct_message(&p, NodeId::new("A")).unwrap_err();
        matches!(err, ReconstructError::UnknownMsgType(_));
    }

    // [边界] peer_message 缺 content 字段
    #[test]
    fn reconstruct_peer_message_missing_content() {
        let p = PendingOutbound {
            msg_type: "peer_message".into(),
            correlation_id: Uuid::new_v4(),
            target_nodes: vec!["B".into()],
            payload: json!({"from_session": "A", "to_session": "B"}),  // no content
            attempt: 1,
        };
        let err = reconstruct_message(&p, NodeId::new("A")).unwrap_err();
        matches!(err, ReconstructError::MissingField(_, "content"));
    }
}