//! Checkpoint evaluation + Route resolution (Phase 6 §2.P3 / task-6.5/6.7).
//!
//! `evaluate` is pure: takes state + rules + routes + graph snapshot + cache,
//! returns a list of `(msg, recipient_ids)` to dispatch. Caller dispatches
//! by `msg.intent()` (Query → park & await; Command → fire-and-forget).

use std::collections::HashMap;
use std::sync::Mutex;

use arf_core::{
    ActionMessage, Capability, Checkpoint, CheckpointRule, NodeId, NodeInfo, Route,
};

use crate::config::AgentConfig;

/// A built Checkpoint message + its resolved recipients.
///
/// Engine decides whether to await (Query) or fire-and-forget (Command).
pub struct CheckpointMsg {
    pub msg: Box<dyn ActionMessage>,
    pub recipients: Vec<NodeId>,
    pub rule_name: String,
}

/// Capability → Vec<NodeId> cache. Phase 6 task 6.7.
///
/// Invariants:
/// - Strict routes bypass this cache (see `resolve_route`).
/// - On `node_online`/`node_offline` signal, caller invokes `invalidate()`.
/// - Cache is internal to Engine — not serialized as part of State.
pub struct DiscoveryCache {
    inner: Mutex<HashMap<Vec<(String, String)>, Vec<NodeId>>>,
}

impl DiscoveryCache {
    pub fn new() -> Self {
        Self {
            inner: Mutex::new(HashMap::new()),
        }
    }

    /// Look up recipients for a Capability; populate cache on miss.
    pub fn get_or_compute(&self, cap: &Capability, graph_nodes: &[NodeInfo]) -> Vec<NodeId> {
        let key = cap.requirements.clone();
        let mut guard = self.inner.lock().expect("DiscoveryCache mutex poisoned");
        if let Some(cached) = guard.get(&key) {
            return cached.clone();
        }
        let resolved: Vec<NodeId> = graph_nodes
            .iter()
            .filter(|n| capability_matches(n, cap))
            .map(|n| n.node_id.clone())
            .collect();
        guard.insert(key, resolved.clone());
        resolved
    }

    /// Clear all cached entries (called on node_online / node_offline).
    pub fn invalidate(&self) {
        self.inner
            .lock()
            .expect("DiscoveryCache mutex poisoned")
            .clear();
    }

    /// Number of cached entries (test hook).
    pub fn len(&self) -> usize {
        self.inner
            .lock()
            .expect("DiscoveryCache mutex poisoned")
            .len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

impl Default for DiscoveryCache {
    fn default() -> Self {
        Self::new()
    }
}

/// Resolve a `Route` to a list of recipient NodeIds. Phase 6 task 6.5/6.7.
///
/// - Strict: returns the explicit NodeIds as-is (no cache lookup).
/// - Discovery: consults `cache`; on miss, queries `graph_nodes` and caches result.
pub fn resolve_route(
    route: &Route,
    graph_nodes: &[NodeInfo],
    cache: &DiscoveryCache,
) -> Vec<NodeId> {
    match route {
        Route::Strict(ids) => ids.clone(),
        Route::Discovery(cap) => cache.get_or_compute(cap, graph_nodes),
    }
}

/// Pure (uncached) resolution — kept for unit testing the matching predicate.
pub fn resolve_route_pure(route: &Route, graph_nodes: &[NodeInfo]) -> Vec<NodeId> {
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

/// Evaluate a single Checkpoint position. Phase 6 task 6.5/6.7.
pub fn evaluate(
    state: &arf_core::State,
    trigger: Checkpoint,
    rules: &[CheckpointRule],
    routes: &HashMap<String, Route>,
    graph_nodes: &[NodeInfo],
    cache: &DiscoveryCache,
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
        let recipients = resolve_route(route, graph_nodes, cache);
        out.push(CheckpointMsg {
            msg,
            recipients,
            rule_name: rule.name.clone(),
        });
    }
    Ok(out)
}