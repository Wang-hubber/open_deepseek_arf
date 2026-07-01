//! PyO3 bindings for ARF V1.x — Bus, ModelAdapter, core types.
//!
//! Async methods use pyo3-async-runtimes to bridge tokio → Python asyncio.

use std::sync::{Arc, OnceLock};

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

pub mod mcp;
pub mod pool;

use arf_bus::{Bus, ConnectError, NodeHandle};
use arf_core::Message as CoreMessage;
use arf_core::{BusGraph, MessageFilter, NodeId, NodeInfo, SendError, SendReceipt, ToMatch};
use arf_core::ModelMessage;
use arf_engine::WaitStrategy;
use arf_model_adapter::{
    AnthropicConfig, AnthropicProvider,
    DeepSeekConfig, DeepSeekProvider,
    MiniMaxConfig, MiniMaxProvider,
    OpenAIConfig, OpenAIProvider,
    ProviderError,
    ModelParams, ModelResponseChunk, ModelResponsePayload,
    ToolCall, ToolCallDelta, ToolDef, Usage,
};
use arf_model_adapter::Provider;
use arf_model_adapter::ModelAdapterNode;
// Phase 6 follow-up 6.22.5: ModelAdapterNode::new() now returns
// `Arc<ModelAdapterNode>` (cheap to clone, shared `Arc<Notify>` for
// shutdown). The rest of this file uses the Arc-wrapped form.
type SharedModelAdapterNode = Arc<ModelAdapterNode>;
use arf_pool::Resource;

pub mod engine;

// ═══════════════════════════════════════════════════════════════════
// Global tokio runtime
// ═══════════════════════════════════════════════════════════════════

pub(crate) fn get_runtime() -> &'static tokio::runtime::Runtime {
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

fn provider_error_to_py(err: ProviderError) -> PyErr {
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
pub struct PyBus {
    pub(crate) inner: Arc<Bus>,
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
// PyModelMessage
// ═══════════════════════════════════════════════════════════════════

/// Python ModelMessage — a single message in model conversation history.
#[pyclass(name = "ModelMessage", from_py_object)]
#[derive(Clone)]
struct PyModelMessage {
    inner: ModelMessage,
}

#[pymethods]
impl PyModelMessage {
    #[new]
    #[pyo3(signature = (role, content, tool_call_id=None, name=None, extra=None))]
    fn new(
        py: Python<'_>,
        role: String,
        content: String,
        tool_call_id: Option<String>,
        name: Option<String>,
        extra: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let extra_json = match extra {
            Some(obj) => py_object_to_json(&obj, py)?,
            None => serde_json::Value::Null,
        };
        Ok(Self {
            inner: ModelMessage {
                role,
                content,
                tool_call_id,
                name,
                tool_calls: Vec::new(),
                extra: extra_json,
            },
        })
    }

    #[getter]
    fn role(&self) -> String {
        self.inner.role.clone()
    }

    #[getter]
    fn content(&self) -> String {
        self.inner.content.clone()
    }

    #[getter]
    fn tool_call_id(&self) -> Option<String> {
        self.inner.tool_call_id.clone()
    }

    #[getter]
    fn name(&self) -> Option<String> {
        self.inner.name.clone()
    }

    #[getter]
    fn extra(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.inner.extra, py)
    }

    fn __repr__(&self) -> String {
        match &self.inner.tool_call_id {
            Some(tc_id) => format!(
                "ModelMessage(role='{}', content='{}...', tool_call_id='{}')",
                self.inner.role,
                &self.inner.content.chars().take(40).collect::<String>(),
                tc_id
            ),
            None => format!(
                "ModelMessage(role='{}', content='{}...')",
                self.inner.role,
                &self.inner.content.chars().take(40).collect::<String>(),
            ),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyModelParams
// ═══════════════════════════════════════════════════════════════════

/// Python ModelParams — inference parameters for a model call.
#[pyclass(name = "ModelParams", from_py_object)]
#[derive(Clone)]
struct PyModelParams {
    inner: ModelParams,
}

#[pymethods]
impl PyModelParams {
    #[new]
    #[pyo3(signature = (temperature=None, max_tokens=None, thinking_enabled=false, extra=None))]
    fn new(
        py: Python<'_>,
        temperature: Option<f32>,
        max_tokens: Option<u32>,
        thinking_enabled: bool,
        extra: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let extra_json = match extra {
            Some(obj) => py_object_to_json(&obj, py)?,
            None => serde_json::Value::Null,
        };
        Ok(Self {
            inner: ModelParams {
                temperature,
                max_tokens,
                thinking_enabled,
                extra: extra_json,
            },
        })
    }

    #[getter]
    fn temperature(&self) -> Option<f32> {
        self.inner.temperature
    }

    #[getter]
    fn max_tokens(&self) -> Option<u32> {
        self.inner.max_tokens
    }

    #[getter]
    fn thinking_enabled(&self) -> bool {
        self.inner.thinking_enabled
    }

    #[getter]
    fn extra(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.inner.extra, py)
    }

    fn __repr__(&self) -> String {
        format!(
            "ModelParams(temperature={:?}, max_tokens={:?}, thinking_enabled={})",
            self.inner.temperature, self.inner.max_tokens, self.inner.thinking_enabled
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyToolDef
// ═══════════════════════════════════════════════════════════════════

/// Python ToolDef — tool/function definition for function calling.
#[pyclass(name = "ToolDef", from_py_object)]
#[derive(Clone)]
struct PyToolDef {
    inner: ToolDef,
}

#[pymethods]
impl PyToolDef {
    #[new]
    #[pyo3(signature = (name, description, parameters))]
    fn new(
        py: Python<'_>,
        name: String,
        description: String,
        parameters: Py<PyAny>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: ToolDef {
                name,
                description,
                parameters: py_object_to_json(&parameters, py)?,
            },
        })
    }

    #[getter]
    fn name(&self) -> String {
        self.inner.name.clone()
    }

    #[getter]
    fn description(&self) -> String {
        self.inner.description.clone()
    }

    #[getter]
    fn parameters(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.inner.parameters, py)
    }

    fn __repr__(&self) -> String {
        format!(
            "ToolDef(name='{}', description='{}')",
            self.inner.name, self.inner.description
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyToolCall
// ═══════════════════════════════════════════════════════════════════

/// Python ToolCall — a tool call request from the model (read-only).
#[pyclass(name = "ToolCall")]
struct PyToolCall {
    inner: ToolCall,
}

#[pymethods]
impl PyToolCall {
    #[getter]
    fn id(&self) -> String {
        self.inner.id.clone()
    }

    #[getter]
    fn name(&self) -> String {
        self.inner.name.clone()
    }

    #[getter]
    fn arguments(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.inner.arguments, py)
    }

    fn __repr__(&self) -> String {
        format!("ToolCall(id='{}', name='{}')", self.inner.id, self.inner.name)
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyToolCallDelta
// ═══════════════════════════════════════════════════════════════════

/// Python ToolCallDelta — incremental tool call update during streaming (read-only).
#[pyclass(name = "ToolCallDelta")]
struct PyToolCallDelta {
    inner: ToolCallDelta,
}

#[pymethods]
impl PyToolCallDelta {
    #[getter]
    fn index(&self) -> u32 {
        self.inner.index
    }

    #[getter]
    fn id(&self) -> Option<String> {
        self.inner.id.clone()
    }

    #[getter]
    fn name(&self) -> Option<String> {
        self.inner.name.clone()
    }

    #[getter]
    fn arguments_delta(&self) -> Option<String> {
        self.inner.arguments_delta.clone()
    }

    fn __repr__(&self) -> String {
        format!(
            "ToolCallDelta(index={}, name={:?})",
            self.inner.index, self.inner.name
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyUsage
// ═══════════════════════════════════════════════════════════════════

/// Python Usage — token usage statistics (read-only).
#[pyclass(name = "Usage")]
struct PyUsage {
    inner: Usage,
}

#[pymethods]
impl PyUsage {
    #[getter]
    fn input_tokens(&self) -> u32 {
        self.inner.input_tokens
    }

    #[getter]
    fn output_tokens(&self) -> u32 {
        self.inner.output_tokens
    }

    #[getter]
    fn total_tokens(&self) -> u32 {
        self.inner.total_tokens
    }

    fn __repr__(&self) -> String {
        format!(
            "Usage(input={}, output={}, total={})",
            self.inner.input_tokens, self.inner.output_tokens, self.inner.total_tokens
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyModelResponseChunk
// ═══════════════════════════════════════════════════════════════════

/// Python ModelResponseChunk — a single chunk in a streaming response (read-only).
#[pyclass(name = "ModelResponseChunk")]
struct PyModelResponseChunk {
    inner: ModelResponseChunk,
}

#[pymethods]
impl PyModelResponseChunk {
    #[getter]
    fn chunk_type(&self) -> String {
        self.inner.chunk_type.clone()
    }

    #[getter]
    fn content(&self) -> Option<String> {
        self.inner.content.clone()
    }

    #[getter]
    fn reasoning(&self) -> Option<String> {
        self.inner.reasoning.clone()
    }

    #[getter]
    fn tool_call(&self) -> Option<PyToolCallDelta> {
        self.inner
            .tool_call
            .clone()
            .map(|tc| PyToolCallDelta { inner: tc })
    }

    #[getter]
    fn usage(&self) -> Option<PyUsage> {
        self.inner.usage.as_ref().map(|u| PyUsage { inner: u.clone() })
    }

    fn __repr__(&self) -> String {
        match self.inner.chunk_type.as_str() {
            "text" => format!(
                "ModelResponseChunk(type='text', content='{}...')",
                self.inner
                    .content
                    .as_deref()
                    .unwrap_or("")
                    .chars()
                    .take(30)
                    .collect::<String>()
            ),
            "reasoning" => format!(
                "ModelResponseChunk(type='reasoning', len={})",
                self.inner.reasoning.as_deref().map_or(0, |r| r.len())
            ),
            _ => format!("ModelResponseChunk(type='{}')", self.inner.chunk_type),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyModelResponsePayload
// ═══════════════════════════════════════════════════════════════════

/// Python ModelResponsePayload — complete model response (read-only).
#[pyclass(name = "ModelResponsePayload")]
struct PyModelResponsePayload {
    inner: ModelResponsePayload,
}

#[pymethods]
impl PyModelResponsePayload {
    #[getter]
    fn message(&self) -> PyModelMessage {
        PyModelMessage {
            inner: self.inner.message.clone(),
        }
    }

    #[getter]
    fn tool_calls(&self) -> Option<Vec<PyToolCall>> {
        self.inner.tool_calls.as_ref().map(|tc_list| {
            tc_list
                .iter()
                .map(|tc| PyToolCall { inner: tc.clone() })
                .collect()
        })
    }

    #[getter]
    fn finish_reason(&self) -> String {
        self.inner.finish_reason.clone()
    }

    #[getter]
    fn usage(&self) -> Option<PyUsage> {
        self.inner.usage.as_ref().map(|u| PyUsage { inner: u.clone() })
    }

    #[getter]
    fn id(&self) -> String {
        self.inner.id.clone()
    }

    #[getter]
    fn model(&self) -> String {
        self.inner.model.clone()
    }

    fn __repr__(&self) -> String {
        format!(
            "ModelResponsePayload(finish_reason='{}', model='{}', usage={:?})",
            self.inner.finish_reason,
            self.inner.model,
            self.inner.usage.as_ref().map(|u| u.total_tokens)
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyDeepSeekConfig
// ═══════════════════════════════════════════════════════════════════

/// Python DeepSeekConfig — configuration for a DeepSeek provider.
#[pyclass(name = "DeepSeekConfig", from_py_object)]
#[derive(Clone)]
struct PyDeepSeekConfig {
    inner: DeepSeekConfig,
}

#[pymethods]
impl PyDeepSeekConfig {
    #[new]
    #[pyo3(signature = (api_key, models, base_url="https://api.deepseek.com".into(), timeout_secs=320, max_retries=3))]
    fn new(
        api_key: String,
        models: Vec<String>,
        base_url: String,
        timeout_secs: u64,
        max_retries: u32,
    ) -> Self {
        Self {
            inner: DeepSeekConfig {
                base_url,
                api_key,
                models,
                timeout_secs,
                max_retries,
            },
        }
    }

    #[getter]
    fn base_url(&self) -> String {
        self.inner.base_url.clone()
    }
    #[getter]
    fn api_key(&self) -> String {
        self.inner.api_key.clone()
    }
    #[getter]
    fn models(&self) -> Vec<String> {
        self.inner.models.clone()
    }
    #[getter]
    fn timeout_secs(&self) -> u64 {
        self.inner.timeout_secs
    }
    #[getter]
    fn max_retries(&self) -> u32 {
        self.inner.max_retries
    }

    fn __repr__(&self) -> String {
        format!(
            "DeepSeekConfig(base_url='{}', models={:?})",
            self.inner.base_url, self.inner.models
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyOpenAIConfig
// ═══════════════════════════════════════════════════════════════════

/// Python OpenAIConfig — configuration for an OpenAI provider.
#[pyclass(name = "OpenAIConfig", from_py_object)]
#[derive(Clone)]
struct PyOpenAIConfig {
    inner: OpenAIConfig,
}

#[pymethods]
impl PyOpenAIConfig {
    #[new]
    #[pyo3(signature = (api_key, models, base_url="https://api.openai.com".into(), timeout_secs=320, max_retries=3))]
    fn new(
        api_key: String,
        models: Vec<String>,
        base_url: String,
        timeout_secs: u64,
        max_retries: u32,
    ) -> Self {
        Self {
            inner: OpenAIConfig {
                base_url,
                api_key,
                models,
                timeout_secs,
                max_retries,
            },
        }
    }

    #[getter]
    fn base_url(&self) -> String {
        self.inner.base_url.clone()
    }
    #[getter]
    fn api_key(&self) -> String {
        self.inner.api_key.clone()
    }
    #[getter]
    fn models(&self) -> Vec<String> {
        self.inner.models.clone()
    }
    #[getter]
    fn timeout_secs(&self) -> u64 {
        self.inner.timeout_secs
    }
    #[getter]
    fn max_retries(&self) -> u32 {
        self.inner.max_retries
    }

    fn __repr__(&self) -> String {
        format!(
            "OpenAIConfig(base_url='{}', models={:?})",
            self.inner.base_url, self.inner.models
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyAnthropicConfig
// ═══════════════════════════════════════════════════════════════════

/// Python AnthropicConfig — configuration for an Anthropic provider.
#[pyclass(name = "AnthropicConfig", from_py_object)]
#[derive(Clone)]
struct PyAnthropicConfig {
    inner: AnthropicConfig,
}

#[pymethods]
impl PyAnthropicConfig {
    #[new]
    #[pyo3(signature = (api_key, models, base_url="https://api.anthropic.com".into(), api_path="/v1/messages".into(), timeout_secs=320, max_retries=3))]
    fn new(
        api_key: String,
        models: Vec<String>,
        base_url: String,
        api_path: String,
        timeout_secs: u64,
        max_retries: u32,
    ) -> Self {
        Self {
            inner: AnthropicConfig {
                base_url,
                api_key,
                models,
                api_path,
                timeout_secs,
                max_retries,
            },
        }
    }

    #[getter]
    fn base_url(&self) -> String {
        self.inner.base_url.clone()
    }
    #[getter]
    fn api_key(&self) -> String {
        self.inner.api_key.clone()
    }
    #[getter]
    fn models(&self) -> Vec<String> {
        self.inner.models.clone()
    }
    #[getter]
    fn api_path(&self) -> String {
        self.inner.api_path.clone()
    }
    #[getter]
    fn timeout_secs(&self) -> u64 {
        self.inner.timeout_secs
    }
    #[getter]
    fn max_retries(&self) -> u32 {
        self.inner.max_retries
    }

    fn __repr__(&self) -> String {
        format!(
            "AnthropicConfig(base_url='{}', api_path='{}', models={:?})",
            self.inner.base_url, self.inner.api_path, self.inner.models
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyMiniMaxConfig
// ═══════════════════════════════════════════════════════════════════

/// Python MiniMaxConfig — configuration for a MiniMax provider.
#[pyclass(name = "MiniMaxConfig", from_py_object)]
#[derive(Clone)]
struct PyMiniMaxConfig {
    inner: MiniMaxConfig,
}

#[pymethods]
impl PyMiniMaxConfig {
    #[staticmethod]
    fn default() -> Self {
        Self {
            inner: MiniMaxConfig::default(),
        }
    }

    #[staticmethod]
    fn from_env() -> PyResult<Self> {
        MiniMaxConfig::from_env()
            .map(|c| Self { inner: c })
            .map_err(provider_error_to_py)
    }

    #[getter]
    fn base_url(&self) -> String {
        self.inner.base_url.clone()
    }
    #[getter]
    fn api_key(&self) -> String {
        self.inner.api_key.clone()
    }
    #[getter]
    fn models(&self) -> Vec<String> {
        self.inner.models.clone()
    }
    #[getter]
    fn timeout_secs(&self) -> u64 {
        self.inner.timeout_secs
    }
    #[getter]
    fn max_retries(&self) -> u32 {
        self.inner.max_retries
    }

    fn __repr__(&self) -> String {
        format!(
            "MiniMaxConfig(base_url='{}', models={:?})",
            self.inner.base_url, self.inner.models
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyDeepSeekProvider
// ═══════════════════════════════════════════════════════════════════

/// Python DeepSeekProvider — DeepSeek API chat completions.
#[pyclass(name = "DeepSeekProvider")]
struct PyDeepSeekProvider {
    inner: Arc<DeepSeekProvider>,
    /// Shared with every `PyModelAdapterNode` returned by `connect_to_bus()`.
    /// Holds the `ModelAdapterNode` instances for the provider's lifetime,
    /// so the node stays alive even if the Python wrapper is dropped
    /// (Phase 6 follow-up 6.22.5 — silent GC death fix).
    connected_nodes: Arc<std::sync::Mutex<Vec<SharedModelAdapterNode>>>,
}

#[pymethods]
impl PyDeepSeekProvider {
    #[new]
    fn new(config: &PyDeepSeekConfig) -> Self {
        Self {
            inner: Arc::new(DeepSeekProvider::new(config.inner.clone())),
            connected_nodes: Arc::new(std::sync::Mutex::new(Vec::<SharedModelAdapterNode>::new())),
        }
    }

    #[getter]
    fn name(&self) -> &str {
        "deepseek"
    }

    #[getter]
    fn supported_models(&self) -> Vec<String> {
        self.inner.supported_models().to_vec()
    }

    /// Side-channel: returns the underlying `Arc<dyn Provider>` so it can
    /// be wrapped by `ModelAdapterResource.from_provider()`. Phase 6 task
    /// 6.22.4. Not part of the public stable API.
    fn _provider_arc(&self) -> crate::pool::PyArcProvider {
        crate::pool::PyArcProvider { arc: self.inner.clone() }
    }

    fn chat<'py>(
        &self,
        py: Python<'py>,
        model_name: String,
        messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>,
        params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|resp| PyModelResponsePayload { inner: resp })
                .map_err(provider_error_to_py)
        })
    }

    fn chat_stream<'py>(
        &self,
        py: Python<'py>,
        model_name: String,
        messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>,
        params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat_stream(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|(chunks, resp)| {
                    let py_chunks: Vec<PyModelResponseChunk> = chunks
                        .into_iter()
                        .map(|c| PyModelResponseChunk { inner: c })
                        .collect();
                    (py_chunks, PyModelResponsePayload { inner: resp })
                })
                .map_err(provider_error_to_py)
        })
    }

    fn connect_to_bus<'py>(
        &self,
        py: Python<'py>,
        bus: &PyBus,
        node_id: PyNodeId,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider: Arc<dyn Provider> = self.inner.clone();
        let bus_ref = bus.inner.clone();
        let nid = node_id.inner;
        // Clone the shared keep-alive vec Arc from the provider. The
        // future pushes the new node into the shared vec (provider
        // always keeps a reference) and returns a PyModelAdapterNode
        // that holds the same Arc (the returned node also keeps a
        // reference). Either side dropping is safe — the other side
        // keeps the ModelAdapterNode alive (Phase 6 follow-up 6.22.5
        // — silent GC death fix).
        let keep_alive = self.connected_nodes.clone();

        future_into_py(py, async move {
            let result = ModelAdapterNode::new(provider, &bus_ref, nid).await;
            match result {
                Ok(node) => {
                    keep_alive.lock().unwrap().push(node.clone());
                    Ok(PyModelAdapterNode {
                        inner: Some(node),
                        keep_alive,
                    })
                }
                Err(e) => Err(PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string())),
            }
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyOpenAIProvider
// ═══════════════════════════════════════════════════════════════════

/// Python OpenAIProvider — standard OpenAI-compatible chat completions.
#[pyclass(name = "OpenAIProvider")]
struct PyOpenAIProvider {
    inner: Arc<OpenAIProvider>,
    /// See `PyDeepSeekProvider::connected_nodes` for rationale.
    connected_nodes: Arc<std::sync::Mutex<Vec<SharedModelAdapterNode>>>,
}

#[pymethods]
impl PyOpenAIProvider {
    #[new]
    fn new(config: &PyOpenAIConfig) -> Self {
        Self {
            inner: Arc::new(OpenAIProvider::new(config.inner.clone())),
            connected_nodes: Arc::new(std::sync::Mutex::new(Vec::<SharedModelAdapterNode>::new())),
        }
    }

    #[getter]
    fn name(&self) -> &str {
        "openai"
    }

    #[getter]
    fn supported_models(&self) -> Vec<String> {
        self.inner.supported_models().to_vec()
    }

    /// Side-channel: returns the underlying `Arc<dyn Provider>` so it can
    /// be wrapped by `ModelAdapterResource.from_provider()`. Phase 6 task
    /// 6.22.4. Not part of the public stable API.
    fn _provider_arc(&self) -> crate::pool::PyArcProvider {
        crate::pool::PyArcProvider { arc: self.inner.clone() }
    }

    fn chat<'py>(
        &self,
        py: Python<'py>,
        model_name: String,
        messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>,
        params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|resp| PyModelResponsePayload { inner: resp })
                .map_err(provider_error_to_py)
        })
    }

    fn chat_stream<'py>(
        &self,
        py: Python<'py>,
        model_name: String,
        messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>,
        params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat_stream(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|(chunks, resp)| {
                    let py_chunks: Vec<PyModelResponseChunk> = chunks
                        .into_iter()
                        .map(|c| PyModelResponseChunk { inner: c })
                        .collect();
                    (py_chunks, PyModelResponsePayload { inner: resp })
                })
                .map_err(provider_error_to_py)
        })
    }

    fn connect_to_bus<'py>(
        &self,
        py: Python<'py>,
        bus: &PyBus,
        node_id: PyNodeId,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider: Arc<dyn Provider> = self.inner.clone();
        let bus_ref = bus.inner.clone();
        let nid = node_id.inner;
        // Clone the shared keep-alive vec Arc from the provider. The
        // future pushes the new node into the shared vec (provider
        // always keeps a reference) and returns a PyModelAdapterNode
        // that holds the same Arc (the returned node also keeps a
        // reference). Either side dropping is safe — the other side
        // keeps the ModelAdapterNode alive (Phase 6 follow-up 6.22.5
        // — silent GC death fix).
        let keep_alive = self.connected_nodes.clone();

        future_into_py(py, async move {
            let result = ModelAdapterNode::new(provider, &bus_ref, nid).await;
            match result {
                Ok(node) => {
                    keep_alive.lock().unwrap().push(node.clone());
                    Ok(PyModelAdapterNode {
                        inner: Some(node),
                        keep_alive,
                    })
                }
                Err(e) => Err(PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string())),
            }
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyAnthropicProvider
// ═══════════════════════════════════════════════════════════════════

/// Python AnthropicProvider — Anthropic Messages API.
#[pyclass(name = "AnthropicProvider")]
struct PyAnthropicProvider {
    inner: Arc<AnthropicProvider>,
    /// See `PyDeepSeekProvider::connected_nodes` for rationale.
    connected_nodes: Arc<std::sync::Mutex<Vec<SharedModelAdapterNode>>>,
}

#[pymethods]
impl PyAnthropicProvider {
    #[new]
    fn new(config: &PyAnthropicConfig) -> Self {
        Self {
            inner: Arc::new(AnthropicProvider::new(config.inner.clone())),
            connected_nodes: Arc::new(std::sync::Mutex::new(Vec::<SharedModelAdapterNode>::new())),
        }
    }

    #[getter]
    fn name(&self) -> &str {
        "anthropic"
    }

    #[getter]
    fn supported_models(&self) -> Vec<String> {
        self.inner.supported_models().to_vec()
    }

    /// Side-channel: returns the underlying `Arc<dyn Provider>` so it can
    /// be wrapped by `ModelAdapterResource.from_provider()`. Phase 6 task
    /// 6.22.4. Not part of the public stable API.
    fn _provider_arc(&self) -> crate::pool::PyArcProvider {
        crate::pool::PyArcProvider { arc: self.inner.clone() }
    }

    fn chat<'py>(
        &self,
        py: Python<'py>,
        model_name: String,
        messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>,
        params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|resp| PyModelResponsePayload { inner: resp })
                .map_err(provider_error_to_py)
        })
    }

    fn chat_stream<'py>(
        &self,
        py: Python<'py>,
        model_name: String,
        messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>,
        params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat_stream(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|(chunks, resp)| {
                    let py_chunks: Vec<PyModelResponseChunk> = chunks
                        .into_iter()
                        .map(|c| PyModelResponseChunk { inner: c })
                        .collect();
                    (py_chunks, PyModelResponsePayload { inner: resp })
                })
                .map_err(provider_error_to_py)
        })
    }

    fn connect_to_bus<'py>(
        &self,
        py: Python<'py>,
        bus: &PyBus,
        node_id: PyNodeId,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider: Arc<dyn Provider> = self.inner.clone();
        let bus_ref = bus.inner.clone();
        let nid = node_id.inner;
        // Clone the shared keep-alive vec Arc from the provider. The
        // future pushes the new node into the shared vec (provider
        // always keeps a reference) and returns a PyModelAdapterNode
        // that holds the same Arc (the returned node also keeps a
        // reference). Either side dropping is safe — the other side
        // keeps the ModelAdapterNode alive (Phase 6 follow-up 6.22.5
        // — silent GC death fix).
        let keep_alive = self.connected_nodes.clone();

        future_into_py(py, async move {
            let result = ModelAdapterNode::new(provider, &bus_ref, nid).await;
            match result {
                Ok(node) => {
                    keep_alive.lock().unwrap().push(node.clone());
                    Ok(PyModelAdapterNode {
                        inner: Some(node),
                        keep_alive,
                    })
                }
                Err(e) => Err(PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string())),
            }
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyMiniMaxProvider
// ═══════════════════════════════════════════════════════════════════

/// Python MiniMaxProvider — MiniMax API chat completions (OpenAI-compatible).
#[pyclass(name = "MiniMaxProvider")]
struct PyMiniMaxProvider {
    inner: Arc<MiniMaxProvider>,
    /// Shared with every `PyModelAdapterNode` returned by `connect_to_bus()`.
    /// Holds the `ModelAdapterNode` instances for the provider's lifetime,
    /// so the node stays alive even if the Python wrapper is dropped
    /// (Phase 6 follow-up 6.22.5 — silent GC death fix).
    connected_nodes: Arc<std::sync::Mutex<Vec<SharedModelAdapterNode>>>,
}

#[pymethods]
impl PyMiniMaxProvider {
    #[new]
    fn new(config: &PyMiniMaxConfig) -> Self {
        Self {
            inner: Arc::new(MiniMaxProvider::new(config.inner.clone())),
            connected_nodes: Arc::new(std::sync::Mutex::new(Vec::<SharedModelAdapterNode>::new())),
        }
    }

    #[getter]
    fn name(&self) -> &str {
        "minimax"
    }

    #[getter]
    fn supported_models(&self) -> Vec<String> {
        self.inner.supported_models().to_vec()
    }

    /// Side-channel: returns the underlying `Arc<dyn Provider>` so it can
    /// be wrapped by `ModelAdapterResource.from_provider()`. Phase 6 task
    /// 6.22.4. Not part of the public stable API.
    fn _provider_arc(&self) -> crate::pool::PyArcProvider {
        crate::pool::PyArcProvider { arc: self.inner.clone() }
    }

    fn chat<'py>(
        &self,
        py: Python<'py>,
        model_name: String,
        messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>,
        params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|resp| PyModelResponsePayload { inner: resp })
                .map_err(provider_error_to_py)
        })
    }

    fn chat_stream<'py>(
        &self,
        py: Python<'py>,
        model_name: String,
        messages: Vec<PyModelMessage>,
        tools: Vec<PyToolDef>,
        params: PyModelParams,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider = self.inner.clone();
        let msgs: Vec<ModelMessage> = messages.into_iter().map(|m| m.inner).collect();
        let tool_defs: Vec<ToolDef> = tools.into_iter().map(|t| t.inner).collect();

        future_into_py(py, async move {
            provider
                .chat_stream(&model_name, msgs, tool_defs, params.inner)
                .await
                .map(|(chunks, resp)| {
                    let py_chunks: Vec<PyModelResponseChunk> = chunks
                        .into_iter()
                        .map(|c| PyModelResponseChunk { inner: c })
                        .collect();
                    (py_chunks, PyModelResponsePayload { inner: resp })
                })
                .map_err(provider_error_to_py)
        })
    }

    fn connect_to_bus<'py>(
        &self,
        py: Python<'py>,
        bus: &PyBus,
        node_id: PyNodeId,
    ) -> PyResult<Bound<'py, PyAny>> {
        let provider: Arc<dyn Provider> = self.inner.clone();
        let bus_ref = bus.inner.clone();
        let nid = node_id.inner;
        // Clone the shared keep-alive vec Arc from the provider. The
        // future pushes the new node into the shared vec (provider
        // always keeps a reference) and returns a PyModelAdapterNode
        // that holds the same Arc (the returned node also keeps a
        // reference). Either side dropping is safe — the other side
        // keeps the ModelAdapterNode alive (Phase 6 follow-up 6.22.5
        // — silent GC death fix).
        let keep_alive = self.connected_nodes.clone();

        future_into_py(py, async move {
            let result = ModelAdapterNode::new(provider, &bus_ref, nid).await;
            match result {
                Ok(node) => {
                    keep_alive.lock().unwrap().push(node.clone());
                    Ok(PyModelAdapterNode {
                        inner: Some(node),
                        keep_alive,
                    })
                }
                Err(e) => Err(PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string())),
            }
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyModelAdapterNode
// ═══════════════════════════════════════════════════════════════════

/// Python ModelAdapterNode — a model adapter connected to the Bus.
///
/// Created by Provider.connect_to_bus(), not constructed directly.
#[pyclass(name = "ModelAdapterNode")]
struct PyModelAdapterNode {
    inner: Option<SharedModelAdapterNode>,
    /// Shared keep-alive handle with the provider's `connected_nodes` vec.
    /// As long as either the provider or the returned node holds the Arc,
    /// the contained `ModelAdapterNode` stays alive. Either side dropping
    /// it is safe — the other side keeps the node alive (Phase 6 follow-up
    /// 6.22.5 — silent GC death fix).
    keep_alive: Arc<std::sync::Mutex<Vec<SharedModelAdapterNode>>>,
}

#[pymethods]
impl PyModelAdapterNode {
    #[getter]
    fn node_id(&self) -> PyResult<PyNodeId> {
        match &self.inner {
            Some(node) => Ok(PyNodeId {
                inner: node.node_id().clone(),
            }),
            None => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "node already shut down",
            )),
        }
    }

    fn shutdown<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let node = self.inner.take().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("node already shut down")
        })?;

        future_into_py(py, async move {
            node.shutdown().await;
            Ok(())
        })
    }

    fn __repr__(&self) -> String {
        match &self.inner {
            Some(node) => format!("ModelAdapterNode(node_id='{}')", node.node_id().as_str()),
            None => "ModelAdapterNode(shut down)".into(),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// Module registration
// ═══════════════════════════════════════════════════════════════════

#[pymodule]
fn _arf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", "1.0.0-alpha.0")?;

    // Phase 1: Bus types
    m.add_class::<PyNodeId>()?;
    m.add_class::<PyMessage>()?;
    m.add_class::<PyNodeInfo>()?;
    m.add_class::<PyToMatch>()?;
    m.add_class::<PyMessageFilter>()?;
    m.add_class::<PySendReceipt>()?;
    m.add_class::<PyBusGraph>()?;
    m.add_class::<PyBus>()?;
    m.add_class::<PyNodeHandle>()?;

    // Phase 4: ModelAdapter types
    m.add_class::<PyModelMessage>()?;
    m.add_class::<PyModelParams>()?;
    m.add_class::<PyToolDef>()?;
    m.add_class::<PyToolCall>()?;
    m.add_class::<PyToolCallDelta>()?;
    m.add_class::<PyUsage>()?;
    m.add_class::<PyModelResponseChunk>()?;
    m.add_class::<PyModelResponsePayload>()?;
    m.add_class::<PyDeepSeekConfig>()?;
    m.add_class::<PyOpenAIConfig>()?;
    m.add_class::<PyAnthropicConfig>()?;
    m.add_class::<PyMiniMaxConfig>()?;
    m.add_class::<PyDeepSeekProvider>()?;
    m.add_class::<PyOpenAIProvider>()?;
    m.add_class::<PyAnthropicProvider>()?;
    m.add_class::<PyMiniMaxProvider>()?;
    m.add_class::<PyModelAdapterNode>()?;

    // Phase 5: MCP
    m.add_class::<mcp::PyRetryConfig>()?;
    m.add_class::<mcp::PyRemoteConfig>()?;
    m.add_class::<mcp::PyMcpNode>()?;

    // Phase 6: Engine
    m.add_class::<engine::PyAgentConfig>()?;
    m.add_class::<engine::PyEngineBuilder>()?;
    m.add_class::<engine::PyEngine>()?;
    m.add_class::<engine::PyState>()?;
    m.add_class::<engine::PyWaitStrategyInner>()?;
    m.add_class::<engine::PyModelCall>()?;

    // Phase 6 task 6.22.4: Checkpoint + Route + CheckpointRule
    m.add_class::<engine::PyCheckpoint>()?;
    m.add_class::<engine::PyActionMessage>()?;
    m.add_class::<engine::PyRoute>()?;
    m.add_class::<engine::PyCapability>()?;
    m.add_class::<engine::PyCheckpointRule>()?;

    // Phase 6 task 6.22.4: Pool
    m.add_class::<pool::PyPoolConfig>()?;
    m.add_class::<pool::PyOverflow>()?;
    m.add_class::<pool::PyPoolError>()?;
    m.add_class::<pool::PyLease>()?;
    m.add_class::<pool::PyModelAdapterResource>()?;
    m.add_class::<pool::PyMcpResource>()?;
    m.add_class::<pool::PyModelAdapterPool>()?;
    m.add_class::<pool::PyMcpPool>()?;

    Ok(())
}
