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
//! `Team.start()` populates every subagent pool by calling
//! `SubagentPool::populate()` so each pool's idle queue is full before
//! the first `delegate()` lands. `Team.stop()` shuts every pool down.
//! Engine lifecycle is owned by the `Arc<Mutex<Engine>>` inside
//! `PyEngineHandle` — dropping the handle triggers Drop, which
//! disconnects from the Bus.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;
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
    /// Start the team: populate every subagent pool's idle queue to `size`,
    /// then flip `started` to true. Idempotent — a second `start()` call
    /// is a no-op (populate() itself is idempotent, and `started` stays
    /// true).
    ///
    /// Pool population is now real (Task 14 review fix): each pool's
    /// idle queue is filled to `size` slots before the first
    /// `delegate()` lands, so the first call doesn't pay a cold-start
    /// latency cost for slot construction.
    ///
    /// Returns an awaitable so the public surface stays async-compatible
    /// (Python callers `await team.start()`).
    fn start<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        // Collect pool Arcs synchronously while we hold the GIL — the
        // pool map is in `self`, the future just needs to call
        // populate() on each.
        let pools: Vec<Arc<SubagentPool>> =
            self.subagent_pools.values().cloned().collect();
        // Mark started up front. If populate() fails on one of the
        // pools, the user can still observe started=True via the
        // getter — but the error is surfaced via the awaitable so the
        // `await` raises.
        self.started = true;
        future_into_py(py, async move {
            for pool in pools {
                pool.populate().await.map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "populate pool failed: {e}"
                    ))
                })?;
            }
            // Return Python None (not `()`) so `await team.start()` is
            // None — matches the pre-fix make_resolved_awaitable contract.
            Ok::<Option<()>, pyo3::PyErr>(None)
        })
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
    /// Returns an awaitable for symmetry with `start()`. The body is
    /// purely synchronous (just dropping refs), so we return a
    /// pre-resolved coroutine rather than spinning up `future_into_py`.
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
    /// Bus-actor wiring: register this pool as a Bus node listening for
    /// `subagent_delegate` messages. The pool replies with
    /// `subagent_result` keyed by `correlation_id`.
    ///
    /// Apps MUST call this once at startup before any
    /// `pool.delegate(...)` — the direct `pool.delegate()` path is gone
    /// (Issue 1: it spawned a `!Send` future from a multi-thread tokio
    /// runtime, leading to the `spawn_local` panic).
    fn connect_to_bus<'py>(
        &self,
        py: Python<'py>,
        pool_id: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let pool = self.inner.clone();
        future_into_py(py, async move {
            let nid = pool.connect_to_bus(pool_id.clone()).await.map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("connect_to_bus failed: {e}"),
                )
            })?;
            Ok(nid.to_string())
        })
    }

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
    ///
    /// ## Bus-actor path (replaces Task 14 !Send direct call)
    ///
    /// Earlier this method called `pool.delegate(...)` directly via
    /// `local_future_into_py`. That panicked with
    /// `spawn_local called from outside of a LocalSet` because
    /// `py-arf`'s tokio runtime is multi-thread — `local_future_into_py`
    /// needs a `LocalSet` context that the multi-thread runtime never
    /// sets up.
    ///
    /// The fix: send a `subagent_delegate` message on the bus to the
    /// pool's node id (registered via `SubagentPool::connect_to_bus`)
    /// and await `subagent_result` keyed by `correlation_id`. The
    /// pool's listener handles the message in its own context.
    fn delegate<'py>(
        &self,
        py: Python<'py>,
        task_input: Py<PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let pool = self.inner.clone();
        let pool_id = self.id.clone();
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
        future_into_py(py, async move {
            use arf_core::{msg_type, Message, NodeId};
            use uuid::Uuid;

            // Bus-handle lookup — pool that hasn't been wired via
            // `connect_to_bus` is unreachable. We surface a clear error
            // so the app knows to call `connect_to_bus` first.
            let bus = pool.bus().clone();

            // Pool id is `subagent-pool/<pool_id>` after connect_to_bus.
            let target = NodeId::new(format!("subagent-pool/{pool_id}"));
            let target_exists = bus
                .graph()
                .nodes
                .iter()
                .any(|n| n.node_id == target && n.node_type == "subagent-pool");
            if !target_exists {
                return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!(
                        "pool '{pool_id}' is not registered on the bus. \
                         Call `await pool.connect_to_bus('{pool_id}')` once at \
                         startup before `delegate()`. \
                         (Legacy `pool.delegate()` direct path was removed; the \
                         pool now only operates as a bus actor — see Phase 7 \
                         architecture note.)"
                    ),
                ));
            }

            // Subscribe a one-shot receiver filtered on subagent_result
            // addressed at us. Doing this BEFORE sending the request
            // avoids the race where the pool replies before we listen.
            let me = NodeId::new(format!("py-arf/pool-handle/{pool_id}"));
            let sub_filter = arf_core::MessageFilter {
                types: Some(vec![msg_type::SUBAGENT_RESULT.into()]),
                to_match: arf_core::ToMatch::DirectedToMe,
            };
            let sub_info = arf_core::NodeInfo {
                node_id: me.clone(),
                node_type: "engine".into(),
                capabilities: serde_json::json!({}),
                online_since: 0,
            };
            let mut sub = bus.connect(sub_info, sub_filter).await.map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("subscribe failed: {e}"),
                )
            })?;

            let cid = Uuid::new_v4();
            // Send the subagent_delegate (SubagentDelegate wire shape).
            let msg = Message::with_from_bus(
                msg_type::SUBAGENT_DELEGATE,
                me.clone(),
                vec![target.clone()],
                serde_json::json!({
                    "correlation_id": cid.to_string(),
                    "task": user_message,
                    "parent_session_id": pool_id,
                }),
                bus.id,
            );
            bus.send(msg).await.map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("send subagent_delegate failed: {e}"),
                )
            })?;

            // Await subagent_result with timeout (typical subagent task
            // finishes much sooner; 60s cap covers slow LLM responses).
            let deadline = std::time::Duration::from_secs(60);
            let result = async {
                loop {
                    let m = sub.recv().await.map_err(|e| {
                        format!("subagent_result recv closed: {e}")
                    })?;
                    if m.msg_type == msg_type::SUBAGENT_RESULT {
                        return Ok::<_, String>(m);
                    }
                }
            };
            let resp = tokio::time::timeout(deadline, result)
                .await
                .map_err(|_| {
                    PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                        "subagent_delegate timed out (60s)",
                    )
                })?
                .map_err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>)?;

            // Validate correlation_id echo (defense against reply for an
            // older in-flight call returning late).
            let echoed = resp
                .payload
                .get("correlation_id")
                .and_then(|v| v.as_str())
                .map(String::from);
            if echoed.as_deref() != Some(&cid.to_string()) {
                return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!(
                        "subagent_result correlation_id mismatch: expected {}, got {:?}",
                        cid, echoed
                    ),
                ));
            }

            // Convert result → Python dict.
            Python::attach(|py| -> PyResult<Py<PyAny>> {
                let dict = pyo3::types::PyDict::new(py);
                let ok = resp
                    .payload
                    .get("ok")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false);
                if !ok {
                    let err = resp
                        .payload
                        .get("error")
                        .and_then(|v| v.as_str())
                        .unwrap_or("(no error message)")
                        .to_string();
                    return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                        err,
                    ));
                }
                let output = resp
                    .payload
                    .get("output")
                    .cloned()
                    .unwrap_or(serde_json::Value::Null);
                let output_str = match &output {
                    serde_json::Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                let turns = resp
                    .payload
                    .get("turns_consumed")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0) as u32;
                let pending = resp
                    .payload
                    .get("pending_peer_messages")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|v| v.as_str().map(String::from))
                            .collect::<Vec<String>>()
                    })
                    .unwrap_or_default();
                dict.set_item("output", output_str)?;
                dict.set_item("turns_consumed", turns)?;
                dict.set_item("pending_peer_messages", pending)?;
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