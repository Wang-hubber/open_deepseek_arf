//! EngineBuilder — build-time fail-fast validation（Phase 7 §1）。

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{BusGraph, NodeId, NodeInfo, Route};

use crate::config::AgentConfig;
use crate::engine::Engine;
use crate::error::BuildError;
use crate::registry::ResourceRegistry;

/// Builds an Engine from AgentConfig + Bus topology.
pub struct EngineBuilder {
    buses: Vec<Arc<Bus>>,
}

impl EngineBuilder {
    pub fn new(buses: Vec<Arc<Bus>>) -> Self {
        Self { buses }
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

        Engine::new(self.buses, config, registry).await
    }
}
