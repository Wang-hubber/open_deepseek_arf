//! ARF MCP — Model Context Protocol node.
//!
//! Each MCP instance is one namespace = one node on the Bus.
//! LocalMcpNode discovers tools/skills from the filesystem;
//! RemoteMcpNode proxies to an external MCP server via HTTP.

pub mod config;
pub mod script;
pub mod skill;
pub mod tool;
pub mod types;

#[cfg(test)]
mod tests;
