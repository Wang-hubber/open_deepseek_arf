//! PyO3 bindings for arf-pool — Pool, PoolConfig, Overflow, PoolError, Lease.
//!
//! Phase 6 task 6.22.4: exposes the bounded-resource lifecycle to Python.
//! Shared types (`PoolConfig`, `Overflow`, `PoolError`, `Lease`) are
//! generic over the resource type; two concrete pools (`ModelAdapterPool`
//! and `McpPool`) wrap `Pool<ModelAdapterResource>` and `Pool<McpResource>`
//! respectively.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

use arf_mcp::{McpNode, McpResource};
use arf_model_adapter::{ModelAdapterResource, Provider};
use arf_pool::{Overflow as CoreOverflow, Pool, PoolConfig, PoolError, Resource};

// ═══════════════════════════════════════════════════════════════════
// PyOverflow
// ═══════════════════════════════════════════════════════════════════

/// Python Overflow — what to do when `Pool.acquire()` is called and all
/// resources are leased.
///
/// Construct via static methods:
///   - `Overflow.Queue(n)` — buffer up to n pending acquirers
///   - `Overflow.Reject` — fail fast with `PoolError.Full`
///   - `Overflow.Block(timeout_secs)` — wait up to `timeout_secs`
#[pyclass(name = "Overflow")]
#[derive(Clone)]
pub struct PyOverflow {
    pub(crate) inner: CoreOverflow,
}

#[pymethods]
impl PyOverflow {
    /// Buffer up to `n` pending acquirers.
    #[staticmethod]
    fn Queue(n: usize) -> Self {
        Self { inner: CoreOverflow::Queue(n) }
    }

    /// Reject immediately with `PoolError.Full`.
    #[staticmethod]
    fn Reject() -> Self {
        Self { inner: CoreOverflow::Reject }
    }

    /// Block up to `timeout_secs` seconds, then `PoolError.Timeout`.
    #[staticmethod]
    fn Block(timeout_secs: f64) -> Self {
        Self {
            inner: CoreOverflow::Block(Duration::from_secs_f64(timeout_secs)),
        }
    }

    fn __repr__(&self) -> String {
        match self.inner {
            CoreOverflow::Queue(n) => format!("Overflow.Queue({})", n),
            CoreOverflow::Reject => "Overflow.Reject".to_string(),
            CoreOverflow::Block(d) => format!("Overflow.Block({:?})", d),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyPoolConfig
// ═══════════════════════════════════════════════════════════════════

/// Python PoolConfig — configuration for a `Pool<R>`.
#[pyclass(name = "PoolConfig")]
#[derive(Clone)]
pub struct PyPoolConfig {
    pub(crate) inner: PoolConfig,
}

#[pymethods]
impl PyPoolConfig {
    #[new]
    #[pyo3(signature = (max_size=4, overflow=None, idle_timeout_secs=None, min_size=0))]
    fn new(
        max_size: usize,
        overflow: Option<PyOverflow>,
        idle_timeout_secs: Option<f64>,
        min_size: usize,
    ) -> Self {
        Self {
            inner: PoolConfig {
                max_size,
                overflow: overflow
                    .map(|o| o.inner)
                    .unwrap_or(CoreOverflow::Queue(0)),
                idle_timeout: idle_timeout_secs.map(Duration::from_secs_f64),
                min_size,
            },
        }
    }

    #[getter]
    fn max_size(&self) -> usize {
        self.inner.max_size
    }

    #[getter]
    fn overflow(&self) -> PyOverflow {
        PyOverflow { inner: self.inner.overflow }
    }

    #[getter]
    fn idle_timeout_secs(&self) -> Option<f64> {
        self.inner.idle_timeout.map(|d| d.as_secs_f64())
    }

    fn __repr__(&self) -> String {
        format!(
            "PoolConfig(max_size={}, overflow={:?}, idle_timeout_secs={:?})",
            self.inner.max_size,
            self.inner.overflow,
            self.inner.idle_timeout.map(|d| d.as_secs_f64())
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyPoolError
// ═══════════════════════════════════════════════════════════════════

/// Python PoolError — raised when Pool.acquire() fails.
///
/// `PoolError` exposes its message via `str(err)`. Variants:
///   - `"pool is full"` — Overflow::Reject, no permits
///   - `"acquire timed out after ..."` — Overflow::Block timeout
///   - `"resource closed"` — drained / closed
///   - `"resource acquire failed: ..."` — underlying error
#[pyclass(name = "PoolError")]
pub struct PyPoolError {
    inner: Option<PoolError>,
}

#[pymethods]
impl PyPoolError {
    /// Construct from a Rust PoolError (used by `pool_error_to_py`).
    #[new]
    fn new(msg: String) -> Self {
        // Best-effort reconstruction: store as Acquire(msg). The Python
        // side only cares about the str() representation.
        Self {
            inner: Some(PoolError::Acquire(msg)),
        }
    }

    fn __str__(&self) -> String {
        self.inner.as_ref().map(|e| e.to_string()).unwrap_or_default()
    }

    fn __repr__(&self) -> String {
        format!("PoolError({})", self.inner.as_ref().map(|e| e.to_string()).unwrap_or_default())
    }
}

/// Helper: convert a `PoolError` into a Python exception.
pub(crate) fn pool_error_to_py(err: PoolError) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyException, _>(err.to_string())
}

// ═══════════════════════════════════════════════════════════════════
// PyLease (opaque handle)
// ═══════════════════════════════════════════════════════════════════

/// Opaque Python handle to a pooled resource.
///
/// The `Lease` is auto-released when this Python object is dropped. To
/// release early, `del lease` or let it go out of scope.
#[pyclass(name = "Lease")]
pub struct PyLease {
    /// Drop hook: releasing happens in the typed wrapper's Drop impl.
    inner: Arc<Mutex<Option<LeaseInner>>>,
}

/// Internal: what kind of resource is held.
pub enum LeaseInner {
    ModelAdapter(arf_pool::Lease<ModelAdapterResource>),
    Mcp(arf_pool::Lease<McpResource>),
}

#[pymethods]
impl PyLease {
    /// Return the kind of the underlying resource (`"model_adapter"` or `"mcp"`).
    fn kind(&self) -> PyResult<String> {
        let guard = self.inner.lock().unwrap();
        let inner = guard.as_ref().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("lease already released")
        })?;
        Ok(match inner {
            LeaseInner::ModelAdapter(l) => l.resource().kind().to_string(),
            LeaseInner::Mcp(l) => l.resource().kind().to_string(),
        })
    }

    fn __repr__(&self) -> String {
        let guard = self.inner.lock().unwrap();
        match guard.as_ref() {
            Some(inner) => match inner {
                LeaseInner::ModelAdapter(_) => "Lease(model_adapter)".to_string(),
                LeaseInner::Mcp(_) => "Lease(mcp)".to_string(),
            },
            None => "Lease(released)".to_string(),
        }
    }
}

// Drop hook: enter the global tokio runtime so the Rust Lease's Drop
// (which calls `tokio::spawn`) doesn't panic on "no reactor running".
impl Drop for PyLease {
    fn drop(&mut self) {
        // Take the inner so the typed Rust Lease is dropped within the
        // tokio runtime context. The Rust Lease's Drop spawns a task to
        // release the resource back to the idle pool.
        let inner = {
            let mut guard = self.inner.lock().unwrap();
            guard.take()
        };
        if let Some(inner) = inner {
            // Enter the global tokio runtime and drop the typed lease
            // inside it. This makes the tokio::spawn inside Rust Lease's
            // Drop succeed.
            let _guard = crate::get_runtime().enter();
            drop(inner);
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyModelAdapterResource
// ═══════════════════════════════════════════════════════════════════

/// Python ModelAdapterResource — a pooled ModelAdapter.
///
/// Construct from a Provider wrapper (DeepSeekProvider / OpenAIProvider /
/// AnthropicProvider / MiniMaxProvider) via `from_provider`.
#[pyclass(name = "ModelAdapterResource")]
pub struct PyModelAdapterResource {
    pub(crate) inner: Arc<ModelAdapterResource>,
}

#[pymethods]
impl PyModelAdapterResource {
    /// Construct from any provider wrapper that exposes `_provider_arc()`.
    ///
    /// The four typed provider bindings (DeepSeekProvider, OpenAIProvider,
    /// AnthropicProvider, MiniMaxProvider) all expose a helper method
    /// `_provider_arc()` that returns the underlying `Arc<dyn Provider>`.
    #[staticmethod]
    fn from_provider(provider: &pyo3::Bound<'_, pyo3::PyAny>) -> PyResult<Self> {
        let arc_obj = provider.call_method0("_provider_arc").map_err(|_| {
            PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                "provider must be a Py-arf Provider wrapper (DeepSeek/OpenAI/Anthropic/MiniMax)",
            )
        })?;
        // The provider's _provider_arc() returns a wrapped Arc<dyn Provider>
        // boxed as PyArcProvider. We extract via downcast.
        let wrapped = arc_obj.extract::<PyArcProvider>().map_err(|_| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "provider._provider_arc() did not return an Arc<dyn Provider>",
            )
        })?;
        let inner = ModelAdapterResource::new(wrapped.arc.clone());
        Ok(Self { inner: Arc::new(inner) })
    }

    #[getter]
    fn kind(&self) -> String {
        self.inner.kind().to_string()
    }

    fn __repr__(&self) -> String {
        format!("ModelAdapterResource(kind={})", self.inner.kind())
    }
}

/// Wraps `Arc<dyn Provider>` so it can cross the PyO3 boundary.
#[pyclass(name = "_ArcProvider")]
pub struct PyArcProvider {
    pub arc: Arc<dyn Provider>,
}

impl Clone for PyArcProvider {
    fn clone(&self) -> Self {
        Self { arc: self.arc.clone() }
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyMcpResource
// ═══════════════════════════════════════════════════════════════════

/// Python McpResource — a pooled McpNode.
#[pyclass(name = "McpResource")]
pub struct PyMcpResource {
    pub(crate) inner: Arc<McpResource>,
}

#[pymethods]
impl PyMcpResource {
    #[new]
    fn new(node: &crate::mcp::PyMcpNode) -> Self {
        Self {
            inner: Arc::new(McpResource::new(node.inner.clone())),
        }
    }

    #[getter]
    fn kind(&self) -> String {
        self.inner.kind().to_string()
    }

    fn __repr__(&self) -> String {
        format!("McpResource(kind={})", self.inner.kind())
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyModelAdapterPool
// ═══════════════════════════════════════════════════════════════════

/// Python ModelAdapterPool — bounded pool of ModelAdapter resources.
#[pyclass(name = "ModelAdapterPool")]
pub struct PyModelAdapterPool {
    pub(crate) inner: Arc<Pool<ModelAdapterResource>>,
}

#[pymethods]
impl PyModelAdapterPool {
    /// Construct a pool with pre-populated resources.
    #[staticmethod]
    fn with_resources(
        config: &PyPoolConfig,
        resources: Vec<PyRef<PyModelAdapterResource>>,
    ) -> Self {
        // The pool requires 'static ownership of `R`. We move fresh
        // `ModelAdapterResource` instances in (cloning the underlying
        // `Arc<dyn Provider>` from each). This means the pool owns its
        // own copies — drop on `with_resources` does not affect them.
        let rcs: Vec<ModelAdapterResource> = resources
            .iter()
            .map(|r| ModelAdapterResource::new(r.inner.provider().clone()))
            .collect();
        let pool = Pool::<ModelAdapterResource>::with_resources(config.inner.clone(), rcs);
        Self { inner: Arc::new(pool) }
    }

    /// Acquire a resource. Returns a `Lease` that auto-releases on drop.
    fn acquire<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let pool = self.inner.clone();
        future_into_py(py, async move {
            pool.acquire().await.map(|lease| PyLease {
                inner: Arc::new(Mutex::new(Some(LeaseInner::ModelAdapter(lease)))),
            }).map_err(pool_error_to_py)
        })
    }

    /// Number of currently idle resources.
    fn idle_count<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let pool = self.inner.clone();
        future_into_py(py, async move { Ok(pool.idle_count().await) })
    }

    /// Total provisioned resources.
    fn total_count<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let pool = self.inner.clone();
        future_into_py(py, async move { Ok(pool.total_count().await) })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyMcpPool
// ═══════════════════════════════════════════════════════════════════

/// Python McpPool — bounded pool of MCP resources.
#[pyclass(name = "McpPool")]
pub struct PyMcpPool {
    pub(crate) inner: Arc<Pool<McpResource>>,
}

#[pymethods]
impl PyMcpPool {
    /// Construct a pool with pre-populated resources.
    #[staticmethod]
    fn with_resources(
        config: &PyPoolConfig,
        resources: Vec<PyRef<PyMcpResource>>,
    ) -> Self {
        let rcs: Vec<McpResource> = resources
            .iter()
            .map(|r| McpResource::new(r.inner.node().clone()))
            .collect();
        let pool = Pool::<McpResource>::with_resources(config.inner.clone(), rcs);
        Self { inner: Arc::new(pool) }
    }

    /// Acquire a resource. Returns a `Lease` that auto-releases on drop.
    fn acquire<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let pool = self.inner.clone();
        future_into_py(py, async move {
            pool.acquire().await.map(|lease| PyLease {
                inner: Arc::new(Mutex::new(Some(LeaseInner::Mcp(lease)))),
            }).map_err(pool_error_to_py)
        })
    }

    /// Number of currently idle resources.
    fn idle_count<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let pool = self.inner.clone();
        future_into_py(py, async move { Ok(pool.idle_count().await) })
    }

    /// Total provisioned resources.
    fn total_count<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let pool = self.inner.clone();
        future_into_py(py, async move { Ok(pool.total_count().await) })
    }
}