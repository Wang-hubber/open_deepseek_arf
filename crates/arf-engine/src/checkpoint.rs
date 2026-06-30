//! Checkpoint evaluation + Route resolution (Phase 6 §2.P3 / task-6.5).
//!
//! `evaluate` is pure: takes state + rules + routes + Bus graph snapshot,
//! returns a list of `(msg, recipient_ids)` to dispatch. Caller dispatches
//! by `msg.intent()` (Query → park & await; Command → fire-and-forget).

use std::collections::HashMap;

use arf_core::{
    ActionMessage, Capability, Checkpoint, CheckpointRule, NodeId, NodeInfo, Route,
};

/// A built Checkpoint message + its resolved recipients.
///
/// Engine decides whether to await (Query) or fire-and-forget (Command).
pub struct CheckpointMsg {
    pub msg: Box<dyn ActionMessage>,
    pub recipients: Vec<NodeId>,
    pub rule_name: String,
}

/// Evaluate a single Checkpoint position: iterate matching rules, call
/// `when(state)`; if true call `build(state)`, look up `routes`
/// by `msg.msg_type()`, resolve to recipient NodeIds via `route` + graph.
///
/// Returns messages ready to dispatch (caller chooses park vs fire-and-forget).
///
/// Returns `Err(RunError::UndeclaredMsgType)` if a built message's msg_type
/// is not registered in routes (programming bug; AgentConfig declaration gap).
pub fn evaluate(
    state: &arf_core::State,
    trigger: Checkpoint,
    rules: &[CheckpointRule],
    routes: &HashMap<String, Route>,
    graph_nodes: &[NodeInfo],
) -> Result<Vec<CheckpointMsg>, crate::error::RunError> {
    use crate::error::RunError;

    let mut out = Vec::new();
    for rule in rules {
        if rule.trigger != trigger {
            continue;
        }
        if !rule.fires(state) {
            continue;
        }
        let msg = rule.build_msg(state);
        let msg_type = msg.msg_type();
        let route = routes.get(msg_type).ok_or_else(|| {
            RunError::UndeclaredMsgType {
                msg_type: msg_type.to_string(),
            }
        })?;
        let recipients = resolve_route(route, graph_nodes);
        out.push(CheckpointMsg {
            msg,
            recipients,
            rule_name: rule.name.clone(),
        });
    }
    Ok(out)
}

/// Resolve a `Route` to a list of recipient NodeIds using the current Bus graph.
///
/// - Strict: returns the explicit NodeIds as-is (Bus.send will reject if offline).
/// - Discovery: filters graph_nodes by Capability AND-match on top-level string
///   fields (Phase 6 §1.3.1).
pub fn resolve_route(route: &Route, graph_nodes: &[NodeInfo]) -> Vec<NodeId> {
    match route {
        Route::Strict(ids) => ids.clone(),
        Route::Discovery(cap) => graph_nodes
            .iter()
            .filter(|n| capability_matches(n, cap))
            .map(|n| n.node_id.clone())
            .collect(),
    }
}

fn capability_matches(node: &NodeInfo, cap: &Capability) -> bool {
    cap.requirements.iter().all(|(k, v)| {
        node.capabilities
            .get(k)
            .and_then(|x| x.as_str())
            == Some(v.as_str())
    })
}
