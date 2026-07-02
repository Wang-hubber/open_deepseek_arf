//! ARF Engine — ReAct runtime loop.

pub mod builder;
pub mod checkpoint;
pub mod config;
pub mod dispatcher;
pub mod engine;
pub mod error;
pub(crate) mod registry;
#[cfg(test)]
mod tests;

pub use arf_agent::{ModelDecl, ResourceSpec};
pub use arf_core::WaitStrategy;
pub use builder::EngineBuilder;
pub use config::{AgentConfig, EngineConfig, MemberFailedAction, OnMemberFailedHandler};
pub use dispatcher::{HandlerContext, HandlerOutcome, HandlerRegistry, MessageHandler};
pub use engine::Engine;
pub use error::{BuildError, RunError};
