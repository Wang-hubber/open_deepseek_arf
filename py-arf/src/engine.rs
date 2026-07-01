//! PyO3 bindings for arf-engine — Engine, AgentConfig, EngineBuilder, CheckpointRule.
//!
//! Phase 6 task 6.10: extends py-arf with Engine types so Python apps can
//! build and run Engines without using arf-agent crate.

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

use arf_core::{
    ActionMessage, Checkpoint, Message, MessageIntent, ModelCall, NodeId, State as CoreState,
};
use arf_engine::{AgentConfig, Engine, EngineBuilder, ModelConfig, WaitStrategy};

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
        agent_id = "agent".to_string(),
        provider = "mock".to_string(),
        model = "mock-v1".to_string(),
        system_prompt_template = "You are helpful.".to_string(),
        max_turns = 10u32,
    ))]
    fn new(
        agent_id: String,
        provider: String,
        model: String,
        system_prompt_template: String,
        max_turns: u32,
    ) -> Self {
        let cfg = AgentConfig {
            agent_id,
            model_config: ModelConfig { provider, model },
            system_prompt_template,
            initial_memory: vec![],
            max_turns,
            tool_timeout_ms: None,
            permissions: Default::default(),
            routes: Default::default(),
            checkpoint_rules: vec![],
            processors: Default::default(),
            on_member_failed: None,
            tools_include: None,
            tools_exclude: vec![],
            skills_include: None,
            skills_exclude: vec![],
        };
        Self {
            inner: std::sync::Arc::new(std::sync::Mutex::new(Some(cfg))),
        }
    }

    #[getter]
    fn agent_id(&self) -> String {
        self.inner.lock().unwrap().as_ref().unwrap().agent_id.clone()
    }

    #[getter]
    fn max_turns(&self) -> u32 {
        self.inner.lock().unwrap().as_ref().unwrap().max_turns
    }

    fn __repr__(&self) -> String {
        let cfg = self.inner.lock().unwrap();
        match cfg.as_ref() {
            Some(c) => format!("AgentConfig(agent_id='{}', max_turns={})", c.agent_id, c.max_turns),
            None => "AgentConfig(consumed)".to_string(),
        }
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