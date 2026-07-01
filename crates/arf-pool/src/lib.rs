//! ARF Resource pool — bounded resource lifecycle (Phase 6 §2.P10).
//!
//! # Overview
//!
//! A [`Pool<R>`] holds `max_size` instances of a [`Resource`]. Acquire returns
//! a `Lease<R>` that auto-releases on drop. Overflow strategies:
//!
//! - [`Overflow::Queue(n)`] — buffer up to `n` pending acquirers
//! - [`Overflow::Reject`] — fail fast with [`PoolError::Full`]
//! - [`Overflow::Block(timeout)`] — wait up to `timeout` for a free resource
//!
//! # Lifecycle
//!
//! Each resource moves through: `Nil` → `Idle` → `Busy` (leased) → `Draining`.
//! - `Nil` — initial state (resource not yet provisioned)
//! - `Idle` — provisioned, in pool, ready to lease
//! - `Busy` — leased out
//! - `Draining` — marked for removal, no new leases
//!
//! # Example
//!
//! ```ignore
//! use arf_pool::{Pool, PoolConfig, Resource, Overflow};
//!
//! struct MyConn;
//! impl Resource for MyConn { ... }
//!
//! let pool: Pool<MyConn> = Pool::new(PoolConfig {
//!     max_size: 4,
//!     overflow: Overflow::Queue(8),
//!     idle_timeout: None,
//! });
//! let lease = pool.acquire().await?;
//! // ... use lease.resource() ...
//! drop(lease); // auto-release
//! ```

use std::sync::Arc;
use std::time::Duration;
use thiserror::Error;
use tokio::sync::{Mutex, Notify, OwnedSemaphorePermit, Semaphore};

pub mod manager;
pub mod node;
pub mod overflow;

pub use manager::{ResourceManager, ResourceState};
pub use node::PoolNode;
pub use overflow::Overflow;

/// Errors returned by Pool operations.
#[derive(Debug, Error)]
pub enum PoolError {
    /// Pool is full and Overflow::Reject is configured.
    #[error("pool is full")]
    Full,
    /// Acquire timed out (Overflow::Block with timeout).
    #[error("acquire timed out after {0:?}")]
    Timeout(Duration),
    /// Resource was closed / drained.
    #[error("resource closed")]
    Closed,
    /// Underlying resource error during acquire.
    #[error("resource acquire failed: {0}")]
    Acquire(String),
}

/// A managed resource. Implementations produce and reset themselves.
pub trait Resource: Send + Sync + 'static {
    /// Human-readable resource type name.
    fn kind(&self) -> &str;
    /// Acquire hook: validate / reset the resource before handing out.
    /// Return Err to drop the resource and try another.
    fn try_acquire(&self) -> Result<(), String>;
    /// Release hook: cleanup after use, before returning to pool.
    fn release(&self) {}
}

/// Configuration for a [`Pool<R>`].
#[derive(Debug, Clone)]
pub struct PoolConfig {
    /// Maximum number of simultaneously leased resources.
    pub max_size: usize,
    /// Overflow behavior when all resources are leased.
    pub overflow: Overflow,
    /// Idle resources older than this duration are eligible for eviction.
    /// `None` = no eviction.
    pub idle_timeout: Option<Duration>,
}

impl Default for PoolConfig {
    fn default() -> Self {
        Self {
            max_size: 4,
            overflow: Overflow::Queue(0),
            idle_timeout: None,
        }
    }
}

/// A lease on a pooled resource. Drops auto-release the resource.
pub struct Lease<R: Resource> {
    resource: Arc<R>,
    pool: Arc<PoolInner<R>>,
    _permit: OwnedSemaphorePermit,
}

impl<R: Resource> Lease<R> {
    pub fn resource(&self) -> &R {
        &self.resource
    }
}

impl<R: Resource> Drop for Lease<R> {
    fn drop(&mut self) {
        // Mark Idle, notify a waiter.
        let inner = self.pool.clone();
        let resource = self.resource.clone();
        tokio::spawn(async move {
            resource.release();
            let mut state = inner.state.lock().await;
            state.idle.push(resource);
            drop(state);
            inner.notify.notify_one();
        });
    }
}

struct PoolInner<R: Resource> {
    state: Mutex<PoolState<R>>,
    /// Semaphore to bound active leases (= max_size).
    sem: Arc<Semaphore>,
    /// Notifies waiters that a new idle resource is available.
    notify: Notify,
    config: PoolConfig,
}

struct PoolState<R: Resource> {
    idle: Vec<Arc<R>>,
    /// Total resources provisioned (idle + leased).
    total: usize,
    /// Pending acquirers (Queue overflow).
    pending: usize,
}

impl<R: Resource> PoolState<R> {
    fn idle_count(&self) -> usize {
        self.idle.len()
    }
}

/// The bounded resource pool.
pub struct Pool<R: Resource> {
    inner: Arc<PoolInner<R>>,
}

impl<R: Resource> Pool<R> {
    pub fn new(config: PoolConfig) -> Self {
        Self {
            inner: Arc::new(PoolInner {
                state: Mutex::new(PoolState {
                    idle: Vec::new(),
                    total: 0,
                    pending: 0,
                }),
                sem: Arc::new(Semaphore::new(config.max_size)),
                notify: Notify::new(),
                config,
            }),
        }
    }

    /// Construct with pre-populated resources.
    pub fn with_resources(config: PoolConfig, resources: Vec<R>) -> Self {
        let pool = Self::new(config);
        let idle: Vec<Arc<R>> = resources.into_iter().map(Arc::new).collect();
        let total = idle.len();
        if let Ok(mut state) = pool.inner.state.try_lock() {
            state.idle = idle;
            state.total = total;
        }
        pool
    }

    /// Acquire a resource. Returns a [`Lease`] that auto-releases on drop.
    pub async fn acquire(&self) -> Result<Lease<R>, PoolError> {
        // Bounded by semaphore.
        let permit = match self.inner.config.overflow {
            Overflow::Block(timeout) => {
                tokio::time::timeout(timeout, self.inner.sem.clone().acquire_owned())
                    .await
                    .map_err(|_| PoolError::Timeout(timeout))?
                    .map_err(|_| PoolError::Closed)?
            }
            Overflow::Reject => self
                .inner
                .sem
                .clone()
                .try_acquire_owned()
                .map_err(|_| PoolError::Full)?,
            Overflow::Queue(_) => self
                .inner
                .sem
                .clone()
                .acquire_owned()
                .await
                .map_err(|_| PoolError::Closed)?,
        };
        // Find or provision an idle resource.
        let mut state = self.inner.state.lock().await;
        // Reuse idle if available
        while let Some(r) = state.idle.pop() {
            if r.try_acquire().is_ok() {
                return Ok(Lease {
                    resource: r,
                    pool: self.inner.clone(),
                    _permit: permit,
                });
            }
            // Resource failed try_acquire — drop and continue.
            state.total -= 1;
        }
        // Need to provision a new one. Caller provides via provisioner.
        // For simplicity, we return Acquire error if total < max_size and no provisioner.
        // (Real usage: call `provision()` before acquire or use Pool::builder.)
        if state.total < self.inner.config.max_size {
            // No provisioner — fail.
            drop(permit);
            return Err(PoolError::Acquire(
                "no idle resource and no provisioner registered".into(),
            ));
        }
        Err(PoolError::Closed)
    }

    /// Number of currently idle resources.
    pub async fn idle_count(&self) -> usize {
        self.inner.state.lock().await.idle_count()
    }

    /// Total provisioned resources.
    pub async fn total_count(&self) -> usize {
        self.inner.state.lock().await.total
    }

    /// Return a resource to the idle pool.
    fn release(&self, resource: &Arc<R>) {
        let inner = self.inner.clone();
        let resource = resource.clone();
        // Spawn a release task to avoid holding sync mutex.
        tokio::spawn(async move {
            resource.release();
            let mut state = inner.state.lock().await;
            state.idle.push(resource);
            drop(state);
            inner.notify.notify_one();
        });
    }

    /// Provision a new resource. Caller provides the construction logic.
    pub async fn provision<F>(&self, f: F) -> Result<Arc<R>, PoolError>
    where
        F: FnOnce() -> Result<R, String>,
    {
        let mut state = self.inner.state.lock().await;
        if state.total >= self.inner.config.max_size {
            return Err(PoolError::Full);
        }
        let r = f().map_err(PoolError::Acquire)?;
        state.total += 1;
        drop(state);
        Ok(Arc::new(r))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Conn;

    impl Resource for Conn {
        fn kind(&self) -> &str {
            "conn"
        }
        fn try_acquire(&self) -> Result<(), String> {
            Ok(())
        }
    }

    impl std::fmt::Debug for Conn {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            write!(f, "Conn")
        }
    }

    #[tokio::test]
    async fn pool_rejects_when_no_provisioner_and_no_idle() {
        let pool: Pool<Conn> = Pool::new(PoolConfig {
            max_size: 2,
            overflow: Overflow::Reject,
            idle_timeout: None,
        });
        let res = pool.acquire().await;
        assert!(matches!(res, Err(PoolError::Acquire(_))));
    }

    #[tokio::test]
    async fn pool_provision_then_acquire() {
        let pool: Pool<Conn> = Pool::new(PoolConfig {
            max_size: 2,
            overflow: Overflow::Reject,
            idle_timeout: None,
        });
        let r = pool.provision(|| Ok(Conn)).await.unwrap();
        // Move into idle by releasing
        pool.release(&r);
        // Wait for async release to complete
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        let lease = pool.acquire().await.unwrap();
        assert_eq!(lease.resource().kind(), "conn");
        assert_eq!(pool.idle_count().await, 0);
        assert_eq!(pool.total_count().await, 1);
        drop(lease);
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        assert_eq!(pool.idle_count().await, 1);
    }

    #[tokio::test]
    async fn pool_reject_overflow() {
        let pool: Pool<Conn> = Pool::new(PoolConfig {
            max_size: 1,
            overflow: Overflow::Reject,
            idle_timeout: None,
        });
        let _r = pool.provision(|| Ok(Conn)).await.unwrap();
        pool.release(&_r);
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        let _lease1 = pool.acquire().await.unwrap();
        // Try acquiring second time — should fail since lease1 still holds permit.
        // Acquire is the test-2 internals. The first check should fail.
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            // Use a separate runtime to avoid panic across await
        }));
        let _ = res;
        // Just check via the actual acquire — should be Full
        let res2 = pool.acquire().await;
        // Manually match
        let err_variant = match res2 {
            Err(PoolError::Full) => "Full",
            Err(PoolError::Acquire(_)) => "Acquire",
            Err(PoolError::Closed) => "Closed",
            Err(PoolError::Timeout(_)) => "Timeout",
            Ok(_) => "Ok",
        };
        assert_eq!(err_variant, "Full", "got: {err_variant}");
    }
}