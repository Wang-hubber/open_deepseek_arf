//! PyO3 bindings for arf-engine — Engine, AgentConfig, EngineBuilder, CheckpointRule.
//!
//! Phase 6 task 6.10: extends py-arf with Engine types so Python apps can
//! build and run Engines without using arf-agent crate.
//!
//! Phase 6 task 6.22.4: also binds CheckpointRule / Checkpoint / Route /
//! Capability / ActionMessage so Python users can wire engine behavior
//! without dropping into Rust.

use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;
use uuid::Uuid;

use arf_core::{
    ActionMessage, Capability, Checkpoint, Message, MessageIntent,
    ModelCall, NodeId, Route, State as CoreState,
};
use arf_core::CheckpointRule as CoreCheckpointRule;
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, WaitStrategy};

use crate::{json_value_to_py, py_object_to_json, PyBus, PyNodeId};

// ═══════════════════════════════════════════════════════════════════
// PyAgentConfig
// ═══════════════════════════════════════════════════════════════════

/// Python AgentConfig — declarative configuration for an Engine.
#[pyclass(name = "AgentConfig")]
#[derive(Clone)]
pub struct PyAgentConfig {
    inner: std::sync::Arc<std::sync::Mutex<Option<AgentConfig>>>,
}

#[pymethods]
impl PyAgentConfig {
    #[new]
    #[pyo3(signature = (
        provider = "deepseek".to_string(),
        model = "deepseek-v4-flash".to_string(),
        endpoint = None,
        api_key_env = None,
        system_prompt_template = "You are a helpful assistant.".to_string(),
        resources = None,
        max_turns = 10u32,
        tool_timeout_ms = None,
        routes = None,
        checkpoint_rules = None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        provider: String,
        model: String,
        endpoint: Option<String>,
        api_key_env: Option<String>,
        system_prompt_template: String,
        resources: Option<Vec<PyResourceSpec>>,
        max_turns: u32,
        tool_timeout_ms: Option<u64>,
        routes: Option<std::collections::HashMap<String, PyRoute>>,
        checkpoint_rules: Option<&pyo3::Bound<'_, pyo3::PyAny>>,
    ) -> PyResult<Self> {
        let routes_map: std::collections::HashMap<String, Route> = match routes {
            Some(m) => m.into_iter().map(|(k, v)| (k, v.inner)).collect(),
            None => std::collections::HashMap::new(),
        };
        let rules: Vec<CoreCheckpointRule> = match checkpoint_rules {
            Some(obj) => {
                let list = obj.cast::<pyo3::types::PyList>()?;
                let mut out = Vec::with_capacity(list.len());
                for item in list.iter() {
                    let rule: PyRef<PyCheckpointRule> = item.extract()?;
                    out.push(rule.into_rust_rule());
                }
                out
            }
            None => vec![],
        };
        let res_specs: Vec<arf_agent::ResourceSpec> = resources
            .unwrap_or_default()
            .into_iter()
            .map(|r| r.inner)
            .collect();
        let cfg = AgentConfig {
            model: arf_agent::ModelDecl {
                provider,
                model_name: model,
                endpoint,
                api_key_env,
                ..Default::default()
            },
            resources: res_specs,
            system_prompt_template,
            initial_memory: vec![],
            allowed_paths: vec![],
            engine: EngineConfig {
                routes: routes_map,
                checkpoint_rules: rules,
                max_turns,
                tool_timeout_ms,
                ..Default::default()
            },
        };
        Ok(Self {
            inner: std::sync::Arc::new(std::sync::Mutex::new(Some(cfg))),
        })
    }

    #[getter]
    fn provider(&self) -> String {
        self.inner.lock().unwrap().as_ref().unwrap().model.provider.clone()
    }

    #[getter]
    fn max_turns(&self) -> u32 {
        self.inner.lock().unwrap().as_ref().unwrap().engine.max_turns
    }

    #[getter]
    fn routes(&self) -> std::collections::HashMap<String, String> {
        self.inner
            .lock()
            .unwrap()
            .as_ref()
            .unwrap()
            .engine
            .routes
            .iter()
            .map(|(k, v)| (k.clone(), format!("{:?}", v)))
            .collect()
    }

    #[getter]
    fn checkpoint_rules(&self, py: Python<'_>) -> PyResult<Vec<pyo3::Py<pyo3::PyAny>>> {
        let cfg = self.inner.lock().unwrap();
        let rules = &cfg.as_ref().unwrap().engine.checkpoint_rules;
        let mut out = Vec::with_capacity(rules.len());
        for r in rules {
            // We can't reconstruct PyCheckpointRule from the Rust
            // CoreCheckpointRule (its actions Vec holds opaque
            // PyActionMessageImpl trait objects, not PyActionMessage).
            // Instead, expose a lightweight summary dict.
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("name", r.name.clone())?;
            dict.set_item("trigger", format!("{:?}", r.trigger))?;
            out.push(dict.into());
        }
        Ok(out)
    }

    fn __repr__(&self) -> String {
        let cfg = self.inner.lock().unwrap();
        match cfg.as_ref() {
            Some(c) => format!("AgentConfig(provider='{}', model='{}', max_turns={})", c.model.provider, c.model.model_name, c.engine.max_turns),
            None => "AgentConfig(consumed)".to_string(),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyResourceSpec
// ═══════════════════════════════════════════════════════════════════

/// Python ResourceSpec — declares a logical resource dependency.
#[pyclass(name = "ResourceSpec")]
#[derive(Clone)]
pub struct PyResourceSpec {
    pub(crate) inner: arf_agent::ResourceSpec,
}

#[pymethods]
impl PyResourceSpec {
    #[new]
    #[pyo3(signature = (name, node_type, capabilities = None))]
    fn new(
        py: Python<'_>,
        name: String,
        node_type: String,
        capabilities: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let caps_json = match capabilities {
            Some(obj) => Some(py_object_to_json(&obj, py)?),
            None => None,
        };
        Ok(Self {
            inner: arf_agent::ResourceSpec {
                name,
                node_type,
                capabilities: caps_json,
            },
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "ResourceSpec(name='{}', node_type='{}')",
            self.inner.name, self.inner.node_type
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyEngineBuilder
// ═══════════════════════════════════════════════════════════════════

/// Python EngineBuilder — build an Engine from a Bus + AgentConfig.
#[pyclass(name = "EngineBuilder")]
pub struct PyEngineBuilder {
    inner: std::sync::Arc<std::sync::Mutex<Option<EngineBuilder>>>,
}

#[pymethods]
impl PyEngineBuilder {
    #[staticmethod]
    fn new(buses: Vec<PyRef<PyBus>>) -> PyResult<Self> {
        if buses.is_empty() {
            return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "EngineBuilder requires at least one bus",
            ));
        }
        let bus_arcs: Vec<Arc<arf_bus::Bus>> = buses.iter().map(|b| b.inner.clone()).collect();
        Ok(Self {
            inner: std::sync::Arc::new(std::sync::Mutex::new(Some(EngineBuilder::new(bus_arcs)))),
        })
    }

    fn build<'py>(
        &self,
        py: Python<'py>,
        config: &PyAgentConfig,
    ) -> PyResult<Bound<'py, PyAny>> {
        let builder_arc = self.inner.clone();
        let config_arc = config.inner.clone();
        // Take builder and config synchronously (under sync mutex) before .await.
        let (builder, cfg) = {
            let mut bguard = builder_arc.lock().unwrap();
            let builder = bguard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("builder already consumed")
            })?;
            let mut cguard = config_arc.lock().unwrap();
            let cfg = cguard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "AgentConfig already used by another build()",
                )
            })?;
            (builder, cfg)
        };

        future_into_py(py, async move {
            builder.build(cfg).await.map(PyEngine::from).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string())
            })
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyEngine
// ═══════════════════════════════════════════════════════════════════

/// Python Engine — ReAct loop actor.
#[pyclass(name = "Engine")]
pub struct PyEngine {
    inner: std::sync::Arc<std::sync::Mutex<Option<Engine>>>,
}

impl PyEngine {
    fn from(engine: Engine) -> Self {
        Self {
            inner: std::sync::Arc::new(std::sync::Mutex::new(Some(engine))),
        }
    }
}

#[pymethods]
impl PyEngine {
    #[getter]
    fn agent_id(&self) -> PyResult<PyNodeId> {
        let guard = self.inner.lock().unwrap();
        match guard.as_ref() {
            Some(e) => Ok(PyNodeId { inner: e.agent_id().clone() }),
            None => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "engine already consumed",
            )),
        }
    }

    #[getter]
    fn system_prompt(&self) -> PyResult<String> {
        let guard = self.inner.lock().unwrap();
        match guard.as_ref() {
            Some(e) => Ok(e.system_prompt().to_string()),
            None => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "engine already consumed",
            )),
        }
    }

    fn run<'py>(
        &self,
        py: Python<'py>,
        state: &PyState,
        user_input: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let engine_arc = self.inner.clone();
        let state_arc = state.inner.clone();
        // Take engine and state synchronously (under sync mutex) before .await.
        let (mut engine, mut state_inner) = {
            let mut eguard = engine_arc.lock().unwrap();
            let engine = eguard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "engine already consumed by a previous run",
                )
            })?;
            let mut sguard = state_arc.lock().unwrap();
            let state_inner = sguard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "state already consumed by a previous run",
                )
            })?;
            (engine, state_inner)
        };

        future_into_py(py, async move {
            let cancel = tokio_util::sync::CancellationToken::new();
            let result = engine
                .run(&mut state_inner, user_input, cancel)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string()));

            // Restore engine and state to PyO3 holders (sync mutex is OK here).
            engine_arc.lock().unwrap().replace(engine);
            state_arc.lock().unwrap().replace(state_inner);
            result
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyState
// ═══════════════════════════════════════════════════════════════════

/// Python State — Engine state holder.
#[pyclass(name = "EngineState")]
pub struct PyState {
    inner: std::sync::Arc<std::sync::Mutex<Option<CoreState>>>,
}

#[pymethods]
impl PyState {
    #[new]
    fn new() -> Self {
        Self {
            inner: std::sync::Arc::new(std::sync::Mutex::new(Some(CoreState::new()))),
        }
    }

    #[getter]
    fn round_count(&self) -> usize {
        self.inner
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| s.over_view.round_count)
            .unwrap_or(0)
    }

    #[getter]
    fn turn_count(&self) -> usize {
        self.inner
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| s.over_view.turn_count)
            .unwrap_or(0)
    }

    #[getter]
    fn context_tokens(&self) -> usize {
        self.inner
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| s.over_view.context_tokens)
            .unwrap_or(0)
    }

    /// Expose `state.messages` to Python as a list of dicts.
    ///
    /// Each dict has shape:
    ///   {role: str, content: str, tool_call_id: str|None, name: str|None,
    ///    tool_calls: list[{id, name, arguments, target}]}
    ///
    /// Phase 6 task 6.22.2: required by Python E2E tests for round-trip
    /// verification (assert state.messages grows / final assistant content
    /// matches engine.run() output).
    #[getter]
    fn messages<'py>(&self, py: Python<'py>) -> PyResult<Vec<Py<PyAny>>> {
        use pyo3::types::PyDict;
        let guard = self.inner.lock().unwrap();
        let state = guard.as_ref().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("state already consumed")
        })?;
        state
            .messages
            .iter()
            .map(|m| {
                let dict = PyDict::new(py);
                dict.set_item("role", m.role.clone())?;
                dict.set_item("content", m.content.clone())?;
                dict.set_item("tool_call_id", m.tool_call_id.clone())?;
                dict.set_item("name", m.name.clone())?;
                let tcs: Vec<Py<PyAny>> = m
                    .tool_calls
                    .iter()
                    .map(|tc| {
                        let d = PyDict::new(py);
                        // `.unwrap()` is intentional — building a fresh dict
                        // with literal string keys cannot fail.
                        d.set_item("id", tc.id.clone()).unwrap();
                        d.set_item("name", tc.name.clone()).unwrap();
                        d.set_item(
                            "arguments",
                            json_value_to_py(&tc.arguments, py).unwrap(),
                        )
                        .unwrap();
                        d.set_item("target", tc.target.as_ref().map(|n| n.as_str().to_string()))
                            .unwrap();
                        d.into()
                    })
                    .collect();
                dict.set_item("tool_calls", tcs)?;
                Ok(dict.into())
            })
            .collect()
    }

    fn __repr__(&self) -> String {
        let guard = self.inner.lock().unwrap();
        match guard.as_ref() {
            Some(s) => format!(
                "EngineState(round={}, turn={}, tokens={})",
                s.over_view.round_count, s.over_view.turn_count, s.over_view.context_tokens
            ),
            None => "EngineState(consumed)".to_string(),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyWaitStrategy
// ═══════════════════════════════════════════════════════════════════

/// Python WaitStrategy — strategy for WaitEvent trigger.
#[pyclass(name = "WaitStrategy", from_py_object)]
#[derive(Clone)]
pub struct PyWaitStrategyInner {
    inner: WaitStrategy,
}

#[pymethods]
impl PyWaitStrategyInner {
    #[classattr]
    fn All() -> Self {
        Self { inner: WaitStrategy::All }
    }

    #[classattr]
    fn Any() -> Self {
        Self { inner: WaitStrategy::Any }
    }

    #[staticmethod]
    fn Count(n: u32) -> Self {
        Self { inner: WaitStrategy::Count(n) }
    }

    fn __eq__(&self, other: &PyWaitStrategyInner) -> bool {
        self.inner == other.inner
    }

    fn __repr__(&self) -> String {
        format!("WaitStrategy({:?})", self.inner)
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyModelCall
// ═══════════════════════════════════════════════════════════════════

/// Python ModelCall — engine → ModelAdapter message (ActionMessage).
#[pyclass(name = "ModelCall")]
pub struct PyModelCall {
    inner: ModelCall,
}

#[pymethods]
impl PyModelCall {
    #[new]
    fn new() -> Self {
        Self { inner: ModelCall::new(vec![]) }
    }

    #[getter]
    fn msg_type(&self) -> &'static str {
        "model_call"
    }

    #[getter]
    fn correlation_id(&self) -> String {
        self.inner.correlation_id().to_string()
    }

    fn __repr__(&self) -> String {
        format!("ModelCall(cid={})", self.inner.correlation_id())
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyCheckpoint
// ═══════════════════════════════════════════════════════════════════

/// Python Checkpoint — 5 invariant positions where Engine may inject
/// side-effect messages (Phase 6 §1.5).
#[pyclass(name = "Checkpoint")]
#[derive(Clone)]
pub struct PyCheckpoint {
    inner: Checkpoint,
}

#[pymethods]
impl PyCheckpoint {
    #[classattr]
    fn BeforeModelCall() -> Self {
        Self { inner: Checkpoint::BeforeModelCall }
    }

    #[classattr]
    fn AfterModelCall() -> Self {
        Self { inner: Checkpoint::AfterModelCall }
    }

    #[classattr]
    fn BeforeToolExec() -> Self {
        Self { inner: Checkpoint::BeforeToolExec }
    }

    #[classattr]
    fn AfterToolExec() -> Self {
        Self { inner: Checkpoint::AfterToolExec }
    }

    #[classattr]
    fn RoundEnd() -> Self {
        Self { inner: Checkpoint::RoundEnd }
    }

    fn __eq__(&self, other: &PyCheckpoint) -> bool {
        self.inner == other.inner
    }

    fn __repr__(&self) -> String {
        format!("Checkpoint.{:?}", self.inner)
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyActionMessage
// ═══════════════════════════════════════════════════════════════════

/// Internal Rust struct that implements ActionMessage for a Python
/// `PyActionMessage`. Stores msg_type + correlation_id + JSON payload,
/// and on `payload()` returns the payload.
pub struct PyActionMessageImpl {
    msg_type: String,
    correlation_id: Uuid,
    payload: serde_json::Value,
}

#[async_trait::async_trait]
impl ActionMessage for PyActionMessageImpl {
    fn msg_type(&self) -> &'static str {
        // SAFETY: we leak the String to get a 'static str. The PyActionMessageImpl
        // lives as long as the CheckpointRule (which is itself 'static for the
        // pool of static strings). This avoids per-call allocation.
        Box::leak(self.msg_type.clone().into_boxed_str())
    }

    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }

    fn payload(&self) -> serde_json::Value {
        self.payload.clone()
    }

    fn intent(&self) -> MessageIntent {
        MessageIntent::Command
    }
}

/// Python ActionMessage — opaque wrapper for embedding in CheckpointRule.
///
/// Construct via `ActionMessage(msg_type=..., correlation_id=..., payload={...})`.
/// The class itself doesn't carry the Rust trait impl — `CheckpointRule`
/// reads its fields at construction time and builds a Rust `ActionMessage`
/// from them.
#[pyclass(name = "ActionMessage")]
#[derive(Clone)]
pub struct PyActionMessage {
    msg_type: String,
    correlation_id: Uuid,
    payload: serde_json::Value,
}

#[pymethods]
impl PyActionMessage {
    #[new]
    #[pyo3(signature = (msg_type, correlation_id=None, payload=None))]
    fn new(
        py: Python<'_>,
        msg_type: String,
        correlation_id: Option<String>,
        payload: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let cid = match correlation_id {
            Some(s) => Uuid::parse_str(&s).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                    "invalid correlation_id UUID: {e}"
                ))
            })?,
            None => Uuid::new_v4(),
        };
        let payload_json = match payload {
            Some(obj) => py_object_to_json(&obj, py)?,
            None => serde_json::json!({"correlation_id": cid.to_string()}),
        };
        Ok(Self {
            msg_type,
            correlation_id: cid,
            payload: payload_json,
        })
    }

    #[getter]
    fn msg_type(&self) -> &str {
        &self.msg_type
    }

    #[getter]
    fn correlation_id(&self) -> String {
        self.correlation_id.to_string()
    }

    #[getter]
    fn payload(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.payload, py)
    }

    fn __repr__(&self) -> String {
        format!(
            "ActionMessage(msg_type='{}', cid={})",
            self.msg_type,
            self.correlation_id
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyRoute
// ═══════════════════════════════════════════════════════════════════

/// Python Route — how Engine delivers a message to its receiver.
#[pyclass(name = "Route")]
#[derive(Clone)]
pub struct PyRoute {
    pub(crate) inner: Route,
}

#[pymethods]
impl PyRoute {
    /// Deliver to exact NodeIds (point-to-point).
    #[staticmethod]
    fn strict(ids: Vec<PyNodeId>) -> Self {
        let inner_ids: Vec<NodeId> = ids.into_iter().map(|n| n.inner).collect();
        Self { inner: Route::strict(inner_ids) }
    }

    /// Deliver to all Nodes whose `capabilities` JSON contains required
    /// key/value pairs (AND).
    #[staticmethod]
    fn discovery(requirements: Vec<(String, String)>) -> Self {
        Self {
            inner: Route::discovery(requirements),
        }
    }

    fn __eq__(&self, other: &PyRoute) -> bool {
        self.inner == other.inner
    }

    fn __repr__(&self) -> String {
        match &self.inner {
            Route::Strict(ids) => format!("Route.Strict({} ids)", ids.len()),
            Route::Discovery(cap) => format!("Route.Discovery({} reqs)", cap.requirements.len()),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyCapability
// ═══════════════════════════════════════════════════════════════════

/// Python Capability — AND-matched key/value pairs declared by Node's
/// `capabilities` JSON.
#[pyclass(name = "Capability")]
#[derive(Clone)]
pub struct PyCapability {
    pub(crate) inner: Capability,
}

#[pymethods]
impl PyCapability {
    #[new]
    fn new(requirements: Vec<(String, String)>) -> Self {
        Self {
            inner: Capability::new(requirements),
        }
    }

    #[getter]
    fn requirements(&self) -> Vec<(String, String)> {
        self.inner.requirements.clone()
    }

    fn __repr__(&self) -> String {
        format!("Capability({} reqs)", self.inner.requirements.len())
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyCheckpointRule
// ═══════════════════════════════════════════════════════════════════

/// Python CheckpointRule — (name, trigger, actions). The Python binding
/// uses a "pre-built actions" model: callers supply a list of
/// `ActionMessage` objects that get cloned into the closure at fire time.
///
/// The `when` predicate defaults to "always fire" (matches Rust default).
/// Phase 6 task 6.22.4: this is the minimum viable Python binding. If
/// users later need a Python `when` callable, the binding can be extended
/// with a Python-side predicate that wraps a `PyAny`.
#[pyclass(name = "CheckpointRule")]
pub struct PyCheckpointRule {
    pub(crate) name: String,
    pub(crate) trigger: Checkpoint,
    pub(crate) actions: Vec<PyActionMessage>,
}

#[pymethods]
impl PyCheckpointRule {
    /// Construct a CheckpointRule from a name + trigger + list of
    /// pre-built ActionMessage instances.
    #[new]
    fn new(name: String, trigger: &PyCheckpoint, actions: Vec<PyActionMessage>) -> Self {
        Self {
            name,
            trigger: trigger.inner,
            actions,
        }
    }

    #[getter]
    fn name(&self) -> String {
        self.name.clone()
    }

    #[getter]
    fn trigger(&self) -> PyCheckpoint {
        PyCheckpoint { inner: self.trigger }
    }

    #[getter]
    fn actions(&self) -> Vec<PyActionMessage> {
        self.actions.clone()
    }

    fn __repr__(&self) -> String {
        format!(
            "CheckpointRule(name='{}', trigger={:?}, actions={})",
            self.name,
            self.trigger,
            self.actions.len()
        )
    }
}

/// Non-pyclass helper for converting PyCheckpointRule → Rust CheckpointRule.
impl PyCheckpointRule {
    /// Convert to a Rust `CheckpointRule`. Used by EngineBuilder to splice
    /// Python-defined rules into the AgentConfig before build().
    pub(crate) fn into_rust_rule(&self) -> CoreCheckpointRule {
        // Clone the actions into a shared Vec<PyActionMessage> that the
        // build closure captures. The closure returns a fresh Rust
        // PyActionMessageImpl per invocation by reading the captured
        // action's fields.
        let actions = Arc::new(self.actions.clone());
        let build_actions = actions.clone();
        CoreCheckpointRule::new(
            self.name.clone(),
            self.trigger,
            |_state| true, // when = always fire
            move |_state| -> Box<dyn ActionMessage> {
                // Pick the first action. (Phase 6 task 6.22.4 minimal API:
                // a single per-rule ActionMessage. Multi-action support
                // requires Engine-side fan-out — future task.)
                let action = build_actions
                    .first()
                    .expect("CheckpointRule requires at least one action");
                Box::new(PyActionMessageImpl {
                    msg_type: action.msg_type.clone(),
                    correlation_id: action.correlation_id,
                    payload: action.payload.clone(),
                })
            },
        )
    }
}