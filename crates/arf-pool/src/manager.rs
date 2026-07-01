//! ResourceManager — lifecycle state machine for a single resource (Phase 6 §2.P10).

/// Lifecycle state of a single pooled resource.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResourceState {
    /// Initial state — resource not yet provisioned.
    Nil,
    /// Provisioned, in pool, ready to lease.
    Idle,
    /// Leased out (held by a [`Lease`](crate::Lease)).
    Busy,
    /// Marked for removal — no new leases; drains when returned.
    Draining,
}

impl ResourceState {
    pub fn is_leaseable(self) -> bool {
        matches!(self, ResourceState::Idle)
    }
}

/// ResourceManager — tracks the state of one resource. Phase 6 task 6.14.
///
/// Note: in this minimal implementation, state is tracked inline in
/// [`Pool`](crate::Pool) via `idle` / `pending` fields. This struct serves
/// as the type-level documentation of the state machine and a future
/// extension point for per-resource metadata (e.g., last-used timestamp).
#[derive(Debug, Clone)]
pub struct ResourceManager {
    state: ResourceState,
    last_used_ms: u64,
    idle_since_ms: u64,
}

impl ResourceManager {
    pub fn new() -> Self {
        Self {
            state: ResourceState::Nil,
            last_used_ms: 0,
            idle_since_ms: 0,
        }
    }

    pub fn state(&self) -> ResourceState {
        self.state
    }

    pub fn last_used_ms(&self) -> u64 {
        self.last_used_ms
    }

    pub fn idle_since_ms(&self) -> u64 {
        self.idle_since_ms
    }

    /// Transition to a new state; returns Err if the transition is invalid.
    pub fn transition(&mut self, to: ResourceState) -> Result<(), &'static str> {
        use ResourceState::*;
        let ok = matches!(
            (self.state, to),
            (Nil, Idle)
                | (Nil, Draining)
                | (Idle, Busy)
                | (Idle, Draining)
                | (Busy, Idle)
                | (Busy, Draining)
                | (Draining, Idle)
        );
        if !ok {
            return Err("invalid state transition");
        }
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;
        if to == Busy {
            self.last_used_ms = now_ms;
        } else if to == Idle {
            self.idle_since_ms = now_ms;
        }
        self.state = to;
        Ok(())
    }
}

impl Default for ResourceManager {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manager_state_transitions() {
        let mut m = ResourceManager::new();
        assert_eq!(m.state(), ResourceState::Nil);
        assert!(m.transition(ResourceState::Idle).is_ok());
        assert!(m.transition(ResourceState::Busy).is_ok());
        assert!(m.transition(ResourceState::Idle).is_ok());
        assert!(m.transition(ResourceState::Draining).is_ok());
        // invalid: Draining → Busy
        assert!(m.transition(ResourceState::Busy).is_err());
    }

    #[test]
    fn manager_invalid_transition() {
        let mut m = ResourceManager::new();
        assert!(m.transition(ResourceState::Busy).is_err(), "Nil → Busy should fail");
    }
}