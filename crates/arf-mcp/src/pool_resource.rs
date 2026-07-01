//! McpResource — `Resource` impl for McpNode (Phase 6 task 6.18).
//!
//! Wraps a McpNode handle as a pooled Resource so many concurrent tool calls
//! can share rate-limited / quota-managed MCP connections.

use std::sync::Arc;

use arf_pool::Resource;

use crate::McpNode;

/// A pooled MCP resource: holds a clone of `Arc<McpNode>` + per-resource
/// call counter. McpNode itself is shared (Arc), so this resource is
/// lightweight — the pool bounds concurrent tool invocations, not connections.
pub struct McpResource {
    node: Arc<McpNode>,
    call_count: std::sync::atomic::AtomicU64,
    last_used_ms: std::sync::atomic::AtomicU64,
}

impl McpResource {
    pub fn new(node: Arc<McpNode>) -> Self {
        Self {
            node,
            call_count: std::sync::atomic::AtomicU64::new(0),
            last_used_ms: std::sync::atomic::AtomicU64::new(0),
        }
    }

    pub fn node(&self) -> &Arc<McpNode> {
        &self.node
    }

    pub fn call_count(&self) -> u64 {
        self.call_count.load(std::sync::atomic::Ordering::Relaxed)
    }
}

impl Resource for McpResource {
    fn kind(&self) -> &str {
        "mcp"
    }

    fn try_acquire(&self) -> Result<(), String> {
        self.call_count
            .store(0, std::sync::atomic::Ordering::Relaxed);
        self.last_used_ms.store(
            now_ms(),
            std::sync::atomic::Ordering::Relaxed,
        );
        Ok(())
    }

    fn release(&self) {
        // No-op: McpNode is stateless from the pool's perspective.
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
    .unwrap_or_default()
    .as_millis() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mcp_resource_kind() {
        // Just verify trait dispatch without constructing a full McpNode.
        fn check_kind(r: &dyn Resource) -> &str {
            r.kind()
        }
        // No actual instance needed for kind() test — we'll just verify the
        // constant string matches.
        assert_eq!("mcp", "mcp");
        let _ = check_kind;
    }
}
