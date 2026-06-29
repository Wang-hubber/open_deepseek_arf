//! PyO3 bindings for arf-mcp — McpNode, RemoteConfig, RetryConfig.

use std::path::PathBuf;
use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3_async_runtimes::tokio::future_into_py;

use arf_mcp::config::{RemoteConfig, RetryConfig};
use arf_mcp::error::McpError;
use arf_mcp::McpNode;

// ═══════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════

fn py_headers_to_hashmap(obj: &pyo3::Bound<'_, pyo3::PyAny>) -> PyResult<std::collections::HashMap<String, String>> {
    let dict = obj.cast::<PyDict>()?;
    let mut map = std::collections::HashMap::new();
    for (k, v) in dict.iter() {
        map.insert(k.extract::<String>()?, v.extract::<String>()?);
    }
    Ok(map)
}

fn mcp_error_to_py(err: McpError) -> PyErr {
    let msg = err.to_string();
    match &err {
        McpError::Discovery { .. } => PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(msg),
        McpError::RemoteUnreachable { .. } => PyErr::new::<pyo3::exceptions::PyConnectionError, _>(msg),
        McpError::RemoteRejected { .. } => PyErr::new::<pyo3::exceptions::PyConnectionError, _>(msg),
        McpError::BusConnect { .. } => PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(msg),
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyRetryConfig
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "RetryConfig", from_py_object)]
#[derive(Clone)]
pub struct PyRetryConfig {
    pub(crate) inner: RetryConfig,
}

#[pymethods]
impl PyRetryConfig {
    #[new]
    #[pyo3(signature = (max_retries=3, initial_backoff_ms=1000, max_backoff_ms=30000))]
    fn new(max_retries: u32, initial_backoff_ms: u64, max_backoff_ms: u64) -> Self {
        Self {
            inner: RetryConfig { max_retries, initial_backoff_ms, max_backoff_ms },
        }
    }

    #[getter]
    fn max_retries(&self) -> u32 { self.inner.max_retries }

    #[getter]
    fn initial_backoff_ms(&self) -> u64 { self.inner.initial_backoff_ms }

    #[getter]
    fn max_backoff_ms(&self) -> u64 { self.inner.max_backoff_ms }

    fn __repr__(&self) -> String {
        format!("RetryConfig(max_retries={}, initial_backoff_ms={}, max_backoff_ms={})",
            self.inner.max_retries, self.inner.initial_backoff_ms, self.inner.max_backoff_ms)
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyRemoteConfig
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "RemoteConfig", from_py_object)]
#[derive(Clone)]
pub struct PyRemoteConfig {
    pub(crate) inner: RemoteConfig,
}

#[pymethods]
impl PyRemoteConfig {
    #[new]
    #[pyo3(signature = (url, transport="http".into(), timeout_secs=None, headers=None, tls_ca_cert=None, retry=None))]
    fn new(
        url: String,
        transport: String,
        timeout_secs: Option<u64>,
        headers: Option<pyo3::Bound<'_, pyo3::PyAny>>,
        tls_ca_cert: Option<String>,
        retry: Option<PyRetryConfig>,
    ) -> PyResult<Self> {
        let hdrs = match headers {
            Some(h) => py_headers_to_hashmap(&h)?,
            None => std::collections::HashMap::new(),
        };
        Ok(Self {
            inner: RemoteConfig {
                transport,
                url,
                timeout_secs,
                headers: hdrs,
                tls_ca_cert: tls_ca_cert.map(PathBuf::from),
                retry: retry.map(|r| r.inner),
            },
        })
    }

    #[getter] fn transport(&self) -> &str { &self.inner.transport }
    #[getter] fn url(&self) -> &str { &self.inner.url }
    #[getter] fn timeout_secs(&self) -> Option<u64> { self.inner.timeout_secs }
    #[getter] fn tls_ca_cert(&self) -> Option<String> { self.inner.tls_ca_cert.as_ref().map(|p| p.display().to_string()) }
    #[getter] fn retry(&self) -> Option<PyRetryConfig> { self.inner.retry.as_ref().map(|r| PyRetryConfig { inner: r.clone() }) }

    fn __repr__(&self) -> String {
        format!("RemoteConfig(url='{}', transport='{}')", self.inner.url, self.inner.transport)
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyMcpNode
// ═══════════════════════════════════════════════════════════════════

/// Python McpNode — unified MCP node (local or remote).
///
///     # Local filesystem scan
///     node = McpNode.local("my-ns", "/path/to/root")
///
///     # Remote HTTP discovery
///     node = await McpNode.remote("codetidy", config)
///
///     # Connect to Bus
///     await node.connect(bus)
#[pyclass(name = "McpNode")]
pub struct PyMcpNode {
    pub(crate) inner: Arc<McpNode>,
}

#[pymethods]
impl PyMcpNode {
    /// Create a local MCP node — scans {root}/tools/ + {root}/skills/.
    #[classmethod]
    fn local(_cls: &pyo3::Bound<'_, pyo3::types::PyType>, namespace: String, root: String) -> PyResult<Self> {
        McpNode::local(&namespace, PathBuf::from(&root))
            .map(|node| Self { inner: node })
            .map_err(mcp_error_to_py)
    }

    /// Create a remote MCP node — HTTP initialize + tools/list (async).
    #[classmethod]
    fn remote<'py>(
        _cls: &pyo3::Bound<'py, pyo3::types::PyType>,
        py: pyo3::Python<'py>,
        namespace: String,
        config: PyRemoteConfig,
    ) -> PyResult<pyo3::Bound<'py, PyAny>> {
        future_into_py(py, async move {
            McpNode::remote(&namespace, config.inner)
                .await
                .map(|node| Self { inner: node })
                .map_err(mcp_error_to_py)
        })
    }

    // ── Properties ──────────────────────────────────────────────

    #[getter]
    fn namespace(&self) -> &str {
        &self.inner.namespace
    }

    #[getter]
    fn node_id(&self) -> String {
        self.inner.node_id.to_string()
    }

    // ── Lifecycle ───────────────────────────────────────────────

    /// Connect to Bus, broadcast node_online, and start the message loop.
    fn connect<'py>(
        &self,
        py: pyo3::Python<'py>,
        bus: &crate::PyBus,
    ) -> PyResult<pyo3::Bound<'py, PyAny>> {
        let node = self.inner.clone();
        let bus_ref = bus.inner.clone();
        future_into_py(py, async move {
            node.connect(&bus_ref).await.map_err(mcp_error_to_py)
        })
    }

    fn __repr__(&self) -> String {
        format!("McpNode(namespace='{}', node_id='{}')",
            &self.inner.namespace, self.inner.node_id)
    }
}
