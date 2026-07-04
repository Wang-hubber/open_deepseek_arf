//! EngineBuilder — build-time fail-fast validation（Phase 7 §1）。

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{BusGraph, NodeId, NodeInfo, Route};
use arf_session::SessionStore;

use crate::config::AgentConfig;
use crate::engine::Engine;
use crate::error::BuildError;
use crate::registry::ResourceRegistry;

/// Builds an Engine from AgentConfig + Bus topology.
pub struct EngineBuilder {
    buses: Vec<Arc<Bus>>,
    session_store: Option<Arc<dyn SessionStore>>,
    session_id: Option<String>,
    /// Phase 9 F-018: explicit NodeId for this Engine instance. Without this,
    /// `Engine::new` defaults to `"engine/{provider}"`, which collides when
    /// multiple Engine instances share the same provider.
    agent_id: Option<NodeId>,
    /// Phase 9 F-019: msg_type names to auto-subscribe to on the bus so the
    /// Engine receives them via its primary subscription's filter.
    auto_subscribe: Vec<String>,
}

impl EngineBuilder {
    pub fn new(buses: Vec<Arc<Bus>>) -> Self {
        Self {
            buses,
            session_store: None,
            session_id: None,
            agent_id: None,
            auto_subscribe: Vec::new(),
        }
    }

    /// Phase 8 task F5: install a session store. Engine will save snapshots
    /// at each of the 5 Checkpoint positions during run().
    pub fn with_session_store(mut self, store: Arc<dyn SessionStore>) -> Self {
        self.session_store = Some(store);
        self
    }

    /// Phase 8 task F5: explicit session id. If None, Engine uses its
    /// `agent_id` as the session id.
    pub fn with_session_id(mut self, session_id: impl Into<String>) -> Self {
        self.session_id = Some(session_id.into());
        self
    }

    /// Phase 9 F-018: override the default agent_id (`"engine/{provider}"`).
    /// Required when multiple Engine instances share the same provider and
    /// would otherwise collide on the bus (bus.connect rejects AlreadyConnected).
    pub fn with_agent_id(mut self, agent_id: NodeId) -> Self {
        self.agent_id = Some(agent_id);
        self
    }

    /// Phase 9 F-019: register extra msg_type names the Engine should receive
    /// (added to its primary subscription's filter). Without this, an Engine
    /// that wants to react to e.g. `"peer_message"` must either set
    /// `cfg.engine.routes["peer_message"]` (Strict route) or hand-subscribe
    /// from outside. This is the lightweight extension point.
    pub fn auto_subscribe_message_types(mut self, types: &[&str]) -> Self {
        self.auto_subscribe.extend(types.iter().map(|s| s.to_string()));
        self
    }

    /// Fail-fast 校验 → create Engine。
    pub async fn build(self, config: AgentConfig) -> Result<Engine, BuildError> {
        if self.buses.is_empty() {
            return Err(BuildError::MissingNodes {
                nodes: vec!["<no bus provided>".into()],
            });
        }

        // 1. aggregate multi-Bus graph → snapshot
        let mut merged: HashMap<NodeId, NodeInfo> = HashMap::new();
        for bus in &self.buses {
            let graph = bus.graph();
            for node in graph.nodes {
                merged.entry(node.node_id.clone()).or_insert(node);
            }
        }

        let snapshot = BusGraph {
            nodes: merged.values().cloned().collect(),
            message_count: 0,
            uptime_ms: 0,
        };

        // 2. resolve declarations → ResourceRegistry
        let registry = ResourceRegistry::build(&config, &snapshot)?;

        // 3. validate custom msg_type routes (config.engine.routes)
        for (_msg_type, route) in &config.engine.routes {
            if let Route::Strict(ids) = route {
                let missing: Vec<String> = ids
                    .iter()
                    .filter(|id| !merged.contains_key(id))
                    .map(|id| id.to_string())
                    .collect();
                if !missing.is_empty() {
                    return Err(BuildError::MissingNodes { nodes: missing });
                }
            }
        }

        // 4. CheckpointRule name 唯一
        let mut seen: HashSet<String> = HashSet::new();
        for rule in &config.engine.checkpoint_rules {
            if !seen.insert(rule.name.clone()) {
                return Err(BuildError::DuplicateRuleName {
                    name: rule.name.clone(),
                });
            }
        }

        let mut engine = Engine::new(self.buses, config, registry, self.agent_id, self.auto_subscribe).await?;
        if let Some(store) = self.session_store {
            let sid = self
                .session_id
                .unwrap_or_else(|| engine.agent_id().to_string());
            engine.install_session_store(store, sid);
        }
        Ok(engine)
    }
}
