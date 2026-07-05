//! Python `SseRelay` — aggregator that turns per-engine JSONL trace
//! files into SSE strings.
//!
//! Phase 7 / V1.x task 7. Today this is a *skeleton*: it lists every
//! member known to the `TeamMembership`, looks for
//! `<storage_root>/events.<member>.jsonl`, and emits a placeholder
//! marker line per existing file. The actual `JsonlTailer` → SSE line
//! conversion is left for a follow-up; the brief explicitly notes the
//! real async tailing is out of scope here.
//!
//! Returning a `future_into_py` future (rather than a synchronous
//! `String`) locks in the async surface so the follow-up doesn't
//! change the public signature.

use std::path::PathBuf;

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

use super::event_filter::PyEventFilter;
use super::team_membership::PyTeamMembership;

/// Python `SseRelay` — high-level aggregation entry point.
#[pyclass(name = "SseRelay")]
pub struct PySseRelay {
    pub(crate) team_membership: Py<PyTeamMembership>,
    pub(crate) storage_root: PathBuf,
    pub(crate) buffer_size: usize,
}

#[pymethods]
impl PySseRelay {
    /// Construct an `SseRelay`.
    ///
    /// Args:
    ///   - `team_membership`: a `TeamMembership` defining which engine
    ///     IDs to aggregate.
    ///   - `storage_root`: directory containing per-engine JSONL trace
    ///     files named `events.<engine_id>.jsonl`.
    ///   - `buffer_size`: reserved — backpressure window per source
    ///     tailer in the real streaming implementation.
    #[new]
    fn new(
        team_membership: Py<PyTeamMembership>,
        storage_root: PathBuf,
        buffer_size: usize,
    ) -> Self {
        Self {
            team_membership,
            storage_root,
            buffer_size,
        }
    }

    /// Async iterator over SSE-formatted strings.
    ///
    /// Skeleton: emits a `// tailer for <member>` marker per member
    /// whose JSONL file exists. A real implementation would construct
    /// a `JsonlTailer` for each member, run them concurrently, format
    /// each event with `SseFormatter`, and apply the filter.
    ///
    /// Implementation note: we accept a `Py<PyEventFilter>` reference
    /// but don't apply it yet — the placeholder loop is fixed-shape.
    /// The signature stays stable so the follow-up can drop in without
    /// breaking callers.
    fn stream<'py>(
        &self,
        py: Python<'py>,
        _filter: Py<PyEventFilter>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let members = self.team_membership.borrow(py).members_rust();
        let storage_root = self.storage_root.clone();
        future_into_py(py, async move {
            let mut outputs: Vec<String> = vec![];
            for m in members {
                let path = storage_root.join(format!("events.{m}.jsonl"));
                if !path.exists() {
                    continue;
                }
                outputs.push(format!("// tailer for {m}\n"));
            }
            Ok::<String, pyo3::PyErr>(outputs.join(""))
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "SseRelay(storage_root='{}', buffer_size={})",
            self.storage_root.display(),
            self.buffer_size
        )
    }
}