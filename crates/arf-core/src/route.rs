//! Route — how Engine delivers a message to its receiver.
//!
//! Phase 6 task 6.1: routing decision type. Single source for both
//! ReAct-driven messages and CheckpointRule-injected messages.

use serde::{Deserialize, Serialize};

use crate::NodeId;

/// Capability: AND-matched key/value pairs declared by Node's `capabilities` JSON.
///
/// "Capability 匹配只看顶层字符串字段；数组/嵌套对象不进 match"
/// — Phase 6 §1.3.1 design constraint.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Capability {
    pub requirements: Vec<(String, String)>,
}

impl Capability {
    pub fn new(requirements: Vec<(String, String)>) -> Self {
        Self { requirements }
    }

    /// Single-key/value convenience constructor.
    pub fn one(key: impl Into<String>, value: impl Into<String>) -> Self {
        Self {
            requirements: vec![(key.into(), value.into())],
        }
    }
}

/// Routing decision for a single msg_type (Phase 6 §1.3).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Route {
    /// Deliver to exact NodeIds (point-to-point).
    Strict(Vec<NodeId>),
    /// Deliver to all Nodes whose `capabilities` JSON contains required key/value pairs (AND).
    Discovery(Capability),
}

impl Route {
    pub fn strict(ids: Vec<NodeId>) -> Self {
        Self::Strict(ids)
    }

    pub fn discovery(reqs: Vec<(String, String)>) -> Self {
        Self::Discovery(Capability::new(reqs))
    }
}
