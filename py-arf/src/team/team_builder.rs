//! Python `TeamBuilder` + `Team` — runtime container for a team's
//! persistent engines and subagent pools.
//!
//! Phase 7 / V1.x task 8 (skeleton) → task 14 (real wiring).
//!
//! `TeamBuilder.build()` now:
//!   - For each `EngineSpec` in `config.persistent_engines`, loads the
//!     referenced agent YAML file into an `AgentConfig`, then builds a
//!     real `Engine` via `EngineBuilder` and stores it in a HashMap
//!     keyed by `engine_id`.
//!   - For each `PoolSpec` in `config.subagent_pools`, loads the
//!     referenced agent YAML file, wraps it in `Arc<AgentConfig>`, and
//!     constructs a real `SubagentPool` stored in a HashMap keyed by
//!     `pool_id`.
//!
//! `Team.start()` populates every subagent pool. `Team.stop()` shuts
//! every pool down. Engine lifecycle is owned by the `Arc<Mutex<Engine>>`
//! inside `PyEngineHandle` — dropping the handle triggers Drop, which
//! disconnects from the Bus.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::{future_into_py, local_future_into_py};
use tokio::sync::Mutex as TokioMutex;

use arf_bus::Bus;
use arf_engine::{AgentConfig, Engine, EngineBuilder};
use arf_subagent_pool::SubagentPool;

use crate::engine::{PyEngineHandle, PyState};

use super::team_config::{PyEngineSpec, PyPoolSpec, PyTeamConfig};

/// Python `TeamBuilder` — assembles a `Team` from a config + bus.
#[pyclass(name = "TeamBuilder")]
pub struct PyTeamBuilder {
    bus: Py<PyAny>,
    config: PyTeamConfig,
}

#[pymethods]
impl PyTeamBuilder {
    /// Construct a builder from a `TeamConfig` and an optional Bus.
    ///
    /// `bus` must be a real `PyBus` for `build()` to actually wire
    /// engines/pools. `None` causes `build()` to fail with a clear
    /// error (no longer silently accepted — that was the skeleton's
    /// hiding spot).
    #[staticmethod]
    fn from_config(bus: Py<PyAny>, config: PyTeamConfig) -> Self {
        Self { bus, config }
    }

    /// Build the `Team` (async — returns an awaitable).
    ///
    /// Constructs one `Engine` per `EngineSpec` and one `SubagentPool`
    /// per `PoolSpec`. Errors (YAML parse / engine build / pool build)
    /// propagate as `PyResult::Err` with a message identifying the
    /// failing engine_id or pool_id.
    fn build<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let bus = self.bus.clone_ref(py);
        let config = self.config.clone();
        // Resolve Bus synchronously while we hold the GIL — Bus is not
        // async to construct, and `PyBus.inner` is already an Arc<Bus>.
        let bus_arc: Arc<Bus> = {
            let bound = bus.bind(py);
            let pybus = bound
                .cast::<crate::PyBus>()
                .map_err(|_| {
                    pyo3::exceptions::PyTypeError::new_err(
                        "TeamBuilder.from_config expects a Bus instance (got None or wrong type)",
                    )
                })?;
            let pyref = pybus.borrow();
            pyref.inner.clone()
        };
        future_into_py(py, async move {
            // ── Build persistent engines ──────────────────────────────
            let mut persistent_engines: HashMap<String, Arc<TokioMutex<Engine>>> =
                HashMap::new();
            for spec in &config.persistent_engines {
                let engine = build_engine_from_spec(spec, &bus_arc).await.map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "build engine '{}': {e}",
                        spec.engine_id
                    ))
                })?;
                persistent_engines.insert(
                    spec.engine_id.clone(),
                    Arc::new(TokioMutex::new(engine)),
                );
            }

            // ── Build subagent pools ──────────────────────────────────
            let mut subagent_pools: HashMap<String, Arc<SubagentPool>> = HashMap::new();
            for spec in &config.subagent_pools {
                let pool = build_pool_from_spec(spec, bus_arc.clone()).await.map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "build pool '{}': {e}",
                        spec.pool_id
                    ))
                })?;
                subagent_pools.insert(spec.pool_id.clone(), Arc::new(pool));
            }

            Ok::<PyTeam, pyo3::PyErr>(PyTeam {
                bus: bus_arc,
                config,
                persistent_engines,
                subagent_pools,
                started: false,
            })
        })
    }

    fn __repr__(&self) -> String {
        format!("TeamBuilder(team_id='{}')", self.config.team_id)
    }
}

/// Python `Team` — runtime container for a team's resources.
///
/// Holds `Arc<Bus>` plus maps of engine and pool handles. After
/// `build()` succeeds, `start()` is the only step that does I/O
/// (populates pools). `stop()` shuts pools down. Engines are dropped
/// when `Team` is dropped (which disconnects them from the Bus).
#[pyclass(name = "Team")]
pub struct PyTeam {
    /// Shared bus reference (Arc so handles can out-live the Team if
    /// the App holds them independently).
    #[allow(dead_code)]
    bus: Arc<Bus>,
    config: PyTeamConfig,
    /// Persistent engines built from `persistent_engines[]` specs.
    /// Keyed by `engine_id`. Wrapped in `Arc<TokioMutex<Engine>>` so
    /// `PyEngineHandle` can lock-and-borrow for `chat(...)` calls
    /// without consuming the engine.
    persistent_engines: HashMap<String, Arc<TokioMutex<Engine>>>,
    /// Subagent pools built from `subagent_pools[]` specs. Keyed by
    /// `pool_id`.
    subagent_pools: HashMap<String, Arc<SubagentPool>>,
    started: bool,
}

#[pymethods]
impl PyTeam {
    /// Start the team: flip `started` to true. Pool population is
    /// lazy — `SubagentPool::delegate()` builds a slot on first call
    /// if none exist, so we don't eagerly populate here. Idempotent —
    /// a second `start()` call is a no-op.
    ///
    /// Returns an awaitable so the public surface stays async-compatible
    /// (Python callers `await team.start()`). The body is purely
    /// synchronous (just toggling a flag), so we return a pre-resolved
    /// `asyncio.Future` rather than a `future_into_py` Send future.
    /// This sidesteps the `!Send` constraint on
    /// `SubagentPool::populate()` (Task 14 limitation).
    fn start<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.started = true;
        make_resolved_awaitable(py, None)
    }

    /// Stop the team: drop every engine handle, clear pool references.
    /// Idempotent — safe to call on a non-started team.
    ///
    /// Note: `SubagentPool::shutdown(self)` consumes self, but pools
    /// are held inside `Arc<SubagentPool>` shared with `PoolHandle`
    /// instances. Calling shutdown would require breaking those shared
    /// references. Instead we drop the team's Arc references; the pool
    /// itself is dropped once every holder (Team + PoolHandle) lets go.
    /// In-flight `delegate()` calls complete on their own.
    ///
    /// Returns an awaitable for symmetry with `start()`.
    fn stop<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        // Drop engine handles — each Engine's `NodeHandle` is dropped,
        // which (via Drop) disconnects from the Bus.
        self.persistent_engines.clear();
        // Drop our pool Arc references. PoolHandles the App holds keep
        // their own Arcs and continue to function until they're dropped.
        self.subagent_pools.clear();
        self.started = false;
        make_resolved_awaitable(py, None)
    }

    /// Return a snapshot of the team's `TeamConfig`.
    #[getter]
    fn config(&self) -> PyTeamConfig {
        self.config.clone()
    }

    /// Whether `start()` has been called.
    #[getter]
    fn started(&self) -> bool {
        self.started
    }

    /// Return an `EngineHandle` for the given persistent engine_id, or
    /// `None` if the engine is not in this team's roster.
    ///
    /// Each call returns a fresh `EngineHandle` that shares the same
    /// `Arc<TokioMutex<Engine>>` with the Team. Multiple HTTP request
    /// handlers can hold handles concurrently; the underlying mutex
    /// serializes engine access.
    fn engine(&self, engine_id: &str) -> Option<PyEngineHandle> {
        let arc = self.persistent_engines.get(engine_id)?;
        Some(PyEngineHandle::from_arc(arc.clone()))
    }

    /// Return a `PoolHandle` for the given pool_id, or `None` if the
    /// pool is not in this team's roster.
    fn subagent_pool(&self, pool_id: &str) -> Option<PyPoolHandle> {
        let arc = self.subagent_pools.get(pool_id)?;
        Some(PyPoolHandle::from_arc(arc.clone(), pool_id.to_string()))
    }

    fn __repr__(&self) -> String {
        format!(
            "Team(team_id='{}', engines={}, pools={}, started={})",
            self.config.team_id,
            self.persistent_engines.len(),
            self.subagent_pools.len(),
            if self.started { "True" } else { "False" }
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyPoolHandle — Python-side wrapper around an Arc<SubagentPool>
// ═══════════════════════════════════════════════════════════════════

/// Python `PoolHandle` — handle to a team subagent pool. Wraps
/// `Arc<SubagentPool>` so the App can call `delegate(task_input)`
/// against any team-owned pool.
#[pyclass(name = "PoolHandle")]
pub struct PyPoolHandle {
    inner: Arc<SubagentPool>,
    /// The pool_id from the team's `PoolSpec` — Task 14 review fix
    /// (previously the `pool_id` getter returned a meaningless `Arc`
    /// pointer string).
    id: String,
}

impl PyPoolHandle {
    fn from_arc(arc: Arc<SubagentPool>, id: String) -> Self {
        Self { inner: arc, id }
    }
}

#[pymethods]
impl PyPoolHandle {
    /// The pool_id from the team's `PoolSpec`. Pre-fix this returned
    /// a meaningless `Arc` pointer string; now it returns the same
    /// id the spec was registered with.
    #[getter]
    fn pool_id(&self) -> String {
        self.id.clone()
    }

    /// Delegate a task to this pool. Returns the assistant's output
    /// (a JSON value or string), turn count, and pending peer messages.
    ///
    /// `task_input` is a Python dict with at least a `user_message`
    /// key (mirrors `TaskInput` from `arf-engine`).
    fn delegate<'py>(
        &self,
        py: Python<'py>,
        task_input: Py<PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let pool = self.inner.clone();
        // Convert the Python dict → TaskInput. Done synchronously while
        // we hold the GIL.
        let bound = task_input.bind(py);
        let dict = bound.cast::<pyo3::types::PyDict>().map_err(|_| {
            pyo3::exceptions::PyTypeError::new_err(
                "task_input must be a dict with at least 'user_message'",
            )
        })?;
        let user_message: String = match dict.get_item("user_message")? {
            Some(v) => v.extract().map_err(|_| {
                pyo3::exceptions::PyTypeError::new_err("'user_message' must be a string")
            })?,
            None => {
                return Err(pyo3::exceptions::PyKeyError::new_err(
                    "missing 'user_message' in task_input",
                ));
            }
        };
        local_future_into_py(py, async move {
            use arf_engine::TaskInput;
            let result = pool
                .delegate(TaskInput { user_message })
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
            // Convert TaskResult → Python dict. The async block runs
            // under the GIL (pymethod guarantees this); we use
            // `Python::attach` to obtain a `Python<'_>` token for
            // building the dict.
            Python::attach(|py| -> PyResult<Py<PyAny>> {
                let dict = pyo3::types::PyDict::new(py);
                let output_str = match &result.output {
                    serde_json::Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                dict.set_item("output", output_str)?;
                dict.set_item("turns_consumed", result.turns_consumed)?;
                dict.set_item("pending_peer_messages", result.pending_peer_messages)?;
                Ok(dict.into())
            })
        })
    }

    fn __repr__(&self) -> String {
        format!("PoolHandle(<{}>)", std::any::type_name::<SubagentPool>())
    }
}

// ═══════════════════════════════════════════════════════════════════
// Internal helpers — load agent YAML + build Engine/SubagentPool
// ═══════════════════════════════════════════════════════════════════

/// Resolve an `EngineSpec`'s `config_path` relative to the team's
/// `bus_id` directory? No — paths in TeamConfig are absolute or
/// relative-to-cwd. We do NOT try to be smart about resolution; the
/// App is expected to pass absolute paths or `cd` to the right
/// directory before calling `build()`.
fn resolve_agent_path(spec_path: &str) -> PathBuf {
    PathBuf::from(spec_path)
}

async fn build_engine_from_spec(
    spec: &PyEngineSpec,
    bus: &Arc<Bus>,
) -> Result<Engine, String> {
    let path = resolve_agent_path(&spec.config_path);
    let raw = std::fs::read_to_string(&path)
        .map_err(|e| format!("read agent config {:?}: {e}", path))?;
    // Reuse the YAML parser added to engine.rs by step 1.
    let cfg: AgentConfig = crate::engine::parse_agent_config_yaml(&raw, &path)
        .map_err(|e| format!("parse agent config {:?}: {e}", path))?;
    // Build the engine with the spec's auto_subscribe types.
    let types: Vec<&str> = spec.auto_subscribe.iter().map(|s| s.as_str()).collect();
    let builder = EngineBuilder::new(vec![bus.clone()])
        .with_agent_id(arf_core::NodeId::new(format!(
            "engine/{}/{}",
            cfg.model.provider, spec.engine_id
        )))
        .auto_subscribe_message_types(&types);
    builder
        .build(cfg)
        .await
        .map_err(|e| format!("EngineBuilder::build failed: {e}"))
}

async fn build_pool_from_spec(
    spec: &PyPoolSpec,
    bus: Arc<Bus>,
) -> Result<SubagentPool, String> {
    let path = resolve_agent_path(&spec.config_path);
    let raw = std::fs::read_to_string(&path)
        .map_err(|e| format!("read agent config {:?}: {e}", path))?;
    let cfg: AgentConfig = crate::engine::parse_agent_config_yaml(&raw, &path)
        .map_err(|e| format!("parse agent config {:?}: {e}", path))?;
    Ok(SubagentPool::new(bus, Arc::new(cfg), spec.size.max(1)))
}

// silence dead_code for PyState import — keep it for downstream Python
// callers that may want to inspect states from the handle API.
#[allow(dead_code)]
fn _state_marker(_: &PyState) {}

/// Build an immediately-resolved awaitable to return from a sync Rust
/// pymethod that needs to satisfy an `async` Python contract.
///
/// We can't use `pyo3_async_runtimes::tokio::future_into_py` because
/// the futures we'd pass through it must be `Send`, and lazy pool
/// population has a `!Send` constraint (see Task 14 limitation note
/// on `Team::start`). Instead we evaluate a tiny inline coroutine
/// via Python and return its (already-completed) `coroutine` object.
///
/// The coroutine is constructed from an `async def` snippet; calling
/// `send(None)` on it raises `StopIteration(None)`, which `await`
/// translates into the resolved value. Returning the coroutine itself
/// (un-started) is fine — Python's `await` machinery calls `send()`
/// on it, drives it to completion, and reads the result. This avoids
/// the need for a running event loop entirely.
fn make_resolved_awaitable(
    py: Python<'_>,
    value: Option<Py<PyAny>>,
) -> PyResult<Bound<'_, PyAny>> {
    // Inline async function definition + call.
    let py_code = c"def _make_awaitable(_value):
    async def _coro():
        return _value
    return _coro()";
    let module = pyo3::types::PyModule::from_code(
        py,
        py_code,
        c"make_awaitable.py",
        c"make_awaitable",
    )?;
    let make = module.getattr("_make_awaitable")?;
    let coro = make.call1((value,))?;
    Ok(coro)
}