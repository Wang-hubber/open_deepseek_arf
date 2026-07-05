//! Python `TeamBuilder` + `Team` — runtime container for a team's
//! persistent engines and subagent pools.
//!
//! Phase 7 / V1.x task 8. The current binding is a **skeleton**:
//!
//!   - `TeamBuilder.from_config(bus, config)` returns a `TeamBuilder`.
//!   - `builder.build()` is `async` (returns a `future_into_py`
//!     awaitable) and constructs a `Team` placeholder. It does **not**
//!     yet instantiate real engines / pools — that wiring lands in a
//!     follow-up task once the engine factory is available.
//!   - `team.start()` / `team.stop()` are async no-ops that flip a
//!     `started` flag, returning `None` once awaited. This locks in
//!     the public async surface so the follow-up does not change the
//!     caller-visible signature.
//!
//! The `bus` parameter on `from_config` is stored as `Py<PyAny>` so the
//! same constructor accepts either a real `PyBus` or `None` during
//! testing. Wiring the bus into engine construction is a follow-up.

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

use super::team_config::PyTeamConfig;

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
    /// `bus` may be `None` for the skeleton; a real `PyBus` will be
    /// passed once engine wiring lands.
    #[staticmethod]
    fn from_config(bus: Py<PyAny>, config: PyTeamConfig) -> Self {
        Self { bus, config }
    }

    /// Build the `Team` (async — returns an awaitable).
    ///
    /// Skeleton: returns a `Team` placeholder carrying the config and
    /// the bus reference. Actual engine / pool construction is a
    /// follow-up.
    fn build<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let bus = self.bus.clone_ref(py);
        let config = self.config.clone();
        future_into_py(py, async move {
            // Real construction (persistent engine spawning + pool
            // population) lands in a follow-up; the skeleton returns
            // a placeholder so the async signature is stable.
            Ok::<PyTeam, pyo3::PyErr>(PyTeam {
                bus,
                config,
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
/// The skeleton tracks only the config and a `started` flag. Real
/// handle storage (`Arc<Engine>`, `Arc<Pool<...>>`) and the actual
/// start/stop side-effects land in a follow-up task.
#[pyclass(name = "Team")]
pub struct PyTeam {
    bus: Py<PyAny>,
    config: PyTeamConfig,
    started: bool,
}

#[pymethods]
impl PyTeam {
    /// Start the team (async no-op skeleton).
    ///
    /// Awaits to `None`; flips `started` to `true` synchronously
    /// before the await so callers can read the state immediately.
    fn start<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.started = true;
        future_into_py(py, async move { Ok::<Option<()>, pyo3::PyErr>(None) })
    }

    /// Stop the team (async no-op skeleton).
    ///
    /// Awaits to `None`. Idempotent — calling on a non-started team
    /// is a no-op.
    fn stop<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        future_into_py(py, async move { Ok::<Option<()>, pyo3::PyErr>(None) })
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

    fn __repr__(&self) -> String {
        format!(
            "Team(team_id='{}', started={})",
            self.config.team_id,
            if self.started { "True" } else { "False" }
        )
    }
}