//! ARF MCP — Model Context Protocol node.
//!
//! Each MCP instance is one namespace = one node on the Bus.
//! LocalMcpNode discovers tools/skills from the filesystem;
//! RemoteMcpNode proxies to an external MCP server via HTTP.

pub mod config;
pub mod discovery;
pub mod error;
pub mod executor;
pub mod node;
pub mod pool_node;
pub mod pool_resource;
pub mod remote;
pub mod runtime;
pub mod script;
pub mod skill;
pub mod tool;
pub mod types;

// Re-export main types for integration tests
pub use node::McpNode;
pub use pool_node::MCPPoolNode;
pub use pool_resource::McpResource;

#[cfg(test)]
mod tests;
