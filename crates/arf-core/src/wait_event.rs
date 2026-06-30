//! WaitEvent — pending message group (Phase 6 §1.7 / §2.P4).
//!
//! One WaitEvent awaits 1+ messages sharing a `correlation_id`. Created
//! by Engine per publish; removed when WaitStrategy triggers.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// When does a WaitEvent fire and unblock Engine's ReAct loop?
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum WaitStrategy {
    /// Fire after every expected member has responded.
    All,
    /// Fire as soon as any one member responds; discard the rest.
    Any,
    /// Fire after N members respond.
    Count(u32),
}

/// One pending message group.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WaitEvent {
    pub id: Uuid,
    /// Each member's response must carry this correlation_id.
    pub correlation_id: Uuid,
    pub strategy: WaitStrategy,
    /// Milliseconds since Unix epoch when this event was created.
    /// (`Instant` isn't serde-friendly; we store wall-clock ms.)
    pub created_at_ms: u64,
    /// Expected member count at time of publish (Strict count or Discovery size).
    pub expected: usize,
}

impl WaitEvent {
    pub fn new(correlation_id: Uuid, strategy: WaitStrategy, expected: usize) -> Self {
        use std::time::{SystemTime, UNIX_EPOCH};
        let created_at_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        Self {
            id: Uuid::new_v4(),
            correlation_id,
            strategy,
            created_at_ms,
            expected,
        }
    }
}
