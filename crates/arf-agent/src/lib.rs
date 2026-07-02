//! ARF AgentConfig — declarative resource configuration skeleton.
//!
//! AgentConfig is a pure data structure that declares WHAT an agent needs:
//! models, tools, subagents, teammates, allowed paths. It uses only logical
//! names and knows nothing about the Bus, NodeIds, or resource availability.
//! Engine (Phase 4) reads AgentConfig and resolves resources at runtime.

mod config;
mod model;
mod resource;
mod tool;

pub use config::{AgentConfig, ConfigError};
pub use model::ModelDecl;
pub use resource::ResourceSpec;
pub use tool::{ToolPermission, ToolSpec};
