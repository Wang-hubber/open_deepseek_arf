//! Overflow strategies for [`Pool`](crate::Pool) acquire() when full.

use std::time::Duration;

/// What to do when [`Pool::acquire`](crate::Pool::acquire) is called and all
/// resources are leased.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Overflow {
    /// Buffer up to `n` pending acquirers. Excess callers get [`PoolError::Full`](crate::PoolError::Full).
    Queue(usize),
    /// Reject immediately with [`PoolError::Full`](crate::PoolError::Full).
    Reject,
    /// Block up to `Duration`, then [`PoolError::Timeout`](crate::PoolError::Timeout).
    Block(Duration),
}

impl Default for Overflow {
    fn default() -> Self {
        Overflow::Queue(0)
    }
}