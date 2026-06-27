//! PyO3 bindings for ARF V1.x — Bus, NodeHandle, core types.
//!
//! Async methods use pyo3-async-runtimes to bridge tokio → Python asyncio.

use std::sync::{Arc, OnceLock};

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

use arf_bus::{Bus, ConnectError, NodeHandle};
use arf_core::Message as CoreMessage;
use arf_core::{BusGraph, MessageFilter, NodeId, NodeInfo, SendError, SendReceipt, ToMatch};

// ═══════════════════════════════════════════════════════════════════
// Global tokio runtime
// ═══════════════════════════════════════════════════════════════════

fn get_runtime() -> &'static tokio::runtime::Runtime {
    static RT: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    RT.get_or_init(|| tokio::runtime::Runtime::new().expect("failed to create tokio runtime"))
}

// ═══════════════════════════════════════════════════════════════════
// JSON ↔ Python conversion
// ═══════════════════════════════════════════════════════════════════

fn json_value_to_py(value: &serde_json::Value, py: Python<'_>) -> PyResult<Py<PyAny>> {
    let json_str = serde_json::to_string(value)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
    let result = py.import("json")?.call_method1("loads", (json_str,))?;
    Ok(result.into())
}

fn py_object_to_json(obj: &Py<PyAny>, py: Python<'_>) -> PyResult<serde_json::Value> {
    let json_str: String = py
        .import("json")?
        .call_method1("dumps", (obj,))?
        .extract()?;
    serde_json::from_str(&json_str)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
}

// ═══════════════════════════════════════════════════════════════════
// Error conversion
// ═══════════════════════════════════════════════════════════════════

fn connect_error_to_py(err: ConnectError) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyException, _>(err.to_string())
}

fn send_error_to_py(err: SendError) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyException, _>(err.to_string())
}

// ═══════════════════════════════════════════════════════════════════
// PyNodeId
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "NodeId", from_py_object)]
#[derive(Clone)]
struct PyNodeId {
    inner: NodeId,
}

#[pymethods]
impl PyNodeId {
    #[new]
    fn new(id: &str) -> Self {
        Self {
            inner: NodeId::new(id),
        }
    }

    fn __str__(&self) -> &str {
        self.inner.as_str()
    }

    fn __repr__(&self) -> String {
        format!("NodeId('{}')", self.inner.as_str())
    }

    fn __eq__(&self, other: &PyNodeId) -> bool {
        self.inner == other.inner
    }

    fn __hash__(&self) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut h = std::collections::hash_map::DefaultHasher::new();
        self.inner.hash(&mut h);
        h.finish()
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyMessage
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "Message")]
struct PyMessage {
    inner: CoreMessage,
}

#[pymethods]
impl PyMessage {
    #[getter]
    fn id(&self) -> String {
        self.inner.id.to_string()
    }

    #[getter]
    fn msg_type(&self) -> &str {
        &self.inner.msg_type
    }

    #[getter]
    fn sender(&self) -> PyNodeId {
        PyNodeId {
            inner: self.inner.from.clone(),
        }
    }

    #[getter]
    fn to(&self) -> Vec<PyNodeId> {
        self.inner
            .to
            .iter()
            .map(|id| PyNodeId { inner: id.clone() })
            .collect()
    }

    #[getter]
    fn payload(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.inner.payload, py)
    }

    #[getter]
    fn timestamp(&self) -> u64 {
        self.inner.timestamp
    }

    fn is_broadcast(&self) -> bool {
        self.inner.is_broadcast()
    }

    fn is_for(&self, node_id: &PyNodeId) -> bool {
        self.inner.is_for(&node_id.inner)
    }

    fn __repr__(&self) -> String {
        format!(
            "Message(id={}, type='{}', sender='{}')",
            self.inner.id, self.inner.msg_type, self.inner.from
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyNodeInfo
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "NodeInfo", from_py_object)]
#[derive(Clone)]
struct PyNodeInfo {
    inner: NodeInfo,
}

#[pymethods]
impl PyNodeInfo {
    #[new]
    #[pyo3(signature = (node_id, node_type, capabilities, online_since=0))]
    fn new(
        py: Python<'_>,
        node_id: String,
        node_type: String,
        capabilities: Py<PyAny>,
        online_since: u64,
    ) -> PyResult<Self> {
        let caps = py_object_to_json(&capabilities, py)?;
        Ok(Self {
            inner: NodeInfo {
                node_id: NodeId::new(node_id),
                node_type,
                capabilities: caps,
                online_since,
            },
        })
    }

    #[getter]
    fn node_id(&self) -> PyNodeId {
        PyNodeId {
            inner: self.inner.node_id.clone(),
        }
    }

    #[getter]
    fn node_type(&self) -> String {
        self.inner.node_type.clone()
    }

    #[getter]
    fn capabilities(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.inner.capabilities, py)
    }

    #[getter]
    fn online_since(&self) -> u64 {
        self.inner.online_since
    }

    fn __repr__(&self) -> String {
        format!(
            "NodeInfo(node_id='{}', type='{}')",
            self.inner.node_id.as_str(),
            self.inner.node_type
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyToMatch
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "ToMatch", from_py_object)]
#[derive(Clone)]
struct PyToMatch {
    inner: ToMatch,
}

#[pymethods]
#[allow(non_snake_case)]
impl PyToMatch {
    #[classattr]
    fn All() -> Self {
        Self {
            inner: ToMatch::All,
        }
    }

    #[classattr]
    fn BroadcastOnly() -> Self {
        Self {
            inner: ToMatch::BroadcastOnly,
        }
    }

    #[classattr]
    fn DirectedToMe() -> Self {
        Self {
            inner: ToMatch::DirectedToMe,
        }
    }

    #[classattr]
    fn BroadcastAndDirectedToMe() -> Self {
        Self {
            inner: ToMatch::BroadcastAndDirectedToMe,
        }
    }

    fn __eq__(&self, other: &PyToMatch) -> bool {
        self.inner == other.inner
    }

    fn __repr__(&self) -> String {
        format!("ToMatch.{:?}", self.inner)
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyMessageFilter
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "MessageFilter", from_py_object)]
#[derive(Clone)]
struct PyMessageFilter {
    inner: MessageFilter,
}

#[pymethods]
impl PyMessageFilter {
    #[new]
    #[pyo3(signature = (types=None, to_match=None))]
    fn new(types: Option<Vec<String>>, to_match: Option<PyToMatch>) -> Self {
        Self {
            inner: MessageFilter {
                types,
                to_match: to_match
                    .map(|t| t.inner)
                    .unwrap_or(ToMatch::BroadcastAndDirectedToMe),
            },
        }
    }

    #[getter]
    fn types(&self) -> Option<Vec<String>> {
        self.inner.types.clone()
    }

    #[getter]
    fn to_match(&self) -> PyToMatch {
        PyToMatch {
            inner: self.inner.to_match.clone(),
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "MessageFilter(types={:?}, to_match={:?})",
            self.inner.types, self.inner.to_match
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PySendReceipt
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "SendReceipt")]
struct PySendReceipt {
    inner: SendReceipt,
}

#[pymethods]
impl PySendReceipt {
    #[getter]
    fn message_id(&self) -> String {
        self.inner.message_id.to_string()
    }

    #[getter]
    fn online_nodes(&self) -> usize {
        self.inner.online_nodes
    }

    #[getter]
    fn matching_nodes(&self) -> usize {
        self.inner.matching_nodes
    }

    fn __repr__(&self) -> String {
        format!(
            "SendReceipt(online={}, matching={})",
            self.inner.online_nodes, self.inner.matching_nodes
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyBusGraph
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "BusGraph")]
struct PyBusGraph {
    inner: BusGraph,
}

#[pymethods]
impl PyBusGraph {
    #[getter]
    fn nodes(&self) -> Vec<PyNodeInfo> {
        self.inner
            .nodes
            .iter()
            .map(|info| PyNodeInfo {
                inner: info.clone(),
            })
            .collect()
    }

    #[getter]
    fn message_count(&self) -> u64 {
        self.inner.message_count
    }

    #[getter]
    fn uptime_ms(&self) -> u64 {
        self.inner.uptime_ms
    }

    fn __repr__(&self) -> String {
        format!(
            "BusGraph(nodes={}, messages={}, uptime_ms={})",
            self.inner.nodes.len(),
            self.inner.message_count,
            self.inner.uptime_ms
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyBus
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "Bus")]
struct PyBus {
    inner: Arc<Bus>,
}

#[pymethods]
impl PyBus {
    #[new]
    #[pyo3(signature = (heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16))]
    fn new(heartbeat_interval_ms: u64, heartbeat_timeout_ms: u64, channel_capacity: usize) -> Self {
        let _guard = get_runtime().enter();
        let bus = Bus::new(
            std::time::Duration::from_millis(heartbeat_interval_ms),
            std::time::Duration::from_millis(heartbeat_timeout_ms),
            channel_capacity,
        );
        Self {
            inner: Arc::new(bus),
        }
    }

    #[getter]
    fn message_count(&self) -> u64 {
        self.inner.message_count()
    }

    #[getter]
    fn uptime_ms(&self) -> u64 {
        self.inner.uptime_ms()
    }

    fn graph(&self) -> PyBusGraph {
        PyBusGraph {
            inner: self.inner.graph(),
        }
    }

    fn connect<'py>(
        &self,
        py: Python<'py>,
        info: PyNodeInfo,
        filter: PyMessageFilter,
    ) -> PyResult<Bound<'py, PyAny>> {
        let bus = self.inner.clone();
        future_into_py(py, async move {
            bus.connect(info.inner, filter.inner)
                .await
                .map(|handle| PyNodeHandle {
                    inner: Arc::new(tokio::sync::Mutex::new(Some(handle))),
                })
                .map_err(connect_error_to_py)
        })
    }

    fn shutdown<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let bus = self.inner.clone();
        future_into_py(py, async move {
            bus.signal_shutdown();
            Ok(())
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyNodeHandle
// ═══════════════════════════════════════════════════════════════════

#[pyclass(name = "NodeHandle")]
struct PyNodeHandle {
    inner: Arc<tokio::sync::Mutex<Option<NodeHandle>>>,
}

#[pymethods]
impl PyNodeHandle {
    fn send<'py>(
        &self,
        py: Python<'py>,
        msg_type: String,
        to: Vec<PyNodeId>,
        payload: Py<PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle_arc = self.inner.clone();
        let to_ids: Vec<NodeId> = to.into_iter().map(|id| id.inner).collect();
        let json_payload = py_object_to_json(&payload, py)?;

        future_into_py(py, async move {
            let guard = handle_arc.lock().await;
            let handle = guard.as_ref().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("node already disconnected")
            })?;

            handle
                .send(&msg_type, to_ids, json_payload)
                .await
                .map(|receipt| PySendReceipt { inner: receipt })
                .map_err(send_error_to_py)
        })
    }

    fn recv<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let handle_arc = self.inner.clone();
        future_into_py(py, async move {
            let mut guard = handle_arc.lock().await;
            let handle = guard.as_mut().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("node already disconnected")
            })?;

            handle
                .recv()
                .await
                .map(|msg| PyMessage { inner: msg })
                .map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyException, _>(format!("recv error: {e}"))
                })
        })
    }

    fn try_recv(&self) -> PyResult<Option<PyMessage>> {
        let mut guard = self.inner.try_lock().map_err(|_| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "concurrent recv in progress — try_recv not available",
            )
        })?;

        let handle = guard.as_mut().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("node already disconnected")
        })?;

        match handle.try_recv() {
            Ok(Some(msg)) => Ok(Some(PyMessage { inner: msg })),
            Ok(None) => Ok(None),
            Err(e) => Err(PyErr::new::<pyo3::exceptions::PyException, _>(format!(
                "try_recv error: {e}"
            ))),
        }
    }

    fn node_info(&self) -> PyResult<PyNodeInfo> {
        let guard = self.inner.try_lock().map_err(|_| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("concurrent access in progress")
        })?;
        let handle = guard.as_ref().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("node already disconnected")
        })?;
        Ok(PyNodeInfo {
            inner: handle.node_info().clone(),
        })
    }

    fn filter_config(&self) -> PyResult<PyMessageFilter> {
        let guard = self.inner.try_lock().map_err(|_| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("concurrent access in progress")
        })?;
        let handle = guard.as_ref().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("node already disconnected")
        })?;
        Ok(PyMessageFilter {
            inner: handle.filter_config().clone(),
        })
    }

    fn disconnect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let handle_arc = self.inner.clone();
        future_into_py(py, async move {
            let mut guard = handle_arc.lock().await;
            let handle = guard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("node already disconnected")
            })?;

            handle.disconnect().await;
            Ok(())
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// Module registration
// ═══════════════════════════════════════════════════════════════════

#[pymodule]
fn _arf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", "1.0.0-alpha.0")?;

    m.add_class::<PyNodeId>()?;
    m.add_class::<PyMessage>()?;
    m.add_class::<PyNodeInfo>()?;
    m.add_class::<PyToMatch>()?;
    m.add_class::<PyMessageFilter>()?;
    m.add_class::<PySendReceipt>()?;
    m.add_class::<PyBusGraph>()?;
    m.add_class::<PyBus>()?;
    m.add_class::<PyNodeHandle>()?;

    Ok(())
}
