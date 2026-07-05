//! Python `SseRelay` — aggregator that turns per-engine JSONL trace
//! files into an SSE stream.
//!
//! Phase 7 / V1.x task 16. The current implementation:
//!
//! 1. Lists every member known to the `TeamMembership`.
//! 2. For each member whose `events.<member>.jsonl` file exists,
//!    constructs a `JsonlTailer` (Task 15) and stores it on a new
//!    `SseRelayStream` (this method).
//! 3. Returns the `PySseRelayStream`. Its `__anext__` lazily spawns
//!    one driver task per tailer (must be inside the asyncio loop
//!    context) and yields SSE-formatted chunks via `SseFormatter`,
//!    filtered by `EventFilter`.
//!
//! Drivers are spawned lazily (not from this synchronous body)
//! because `pyo3_async_runtimes::tokio::into_future` requires a
//! running Python asyncio event loop, which is only attached inside
//! `future_into_py`'s context.

use std::path::PathBuf;
use std::sync::Arc;
use std::sync::Mutex as StdMutex;

use pyo3::prelude::*;
use tokio::sync::mpsc;

use super::event_filter::PyEventFilter;
use super::jsonl_tailer::PyJsonlTailer;
use super::sse_relay_stream::PySseRelayStream;
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
    ///   - `buffer_size`: backpressure window for the merged channel
    ///     (i.e. the `mpsc::channel` capacity). Larger = more
    ///     buffering when consumers fall behind.
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

    /// Construct an `SseRelayStream` that merges JSONL events from
    /// every team member's `events.<member>.jsonl` file and yields
    /// SSE-formatted chunks.
    ///
    /// `filter` is consulted once per event: events whose
    /// `(engine_id, msg_type)` doesn't match are silently dropped.
    /// Use a fully-open filter (`engine_ids=None, msg_types=None`) to
    /// receive every event.
    ///
    /// Returns a `PySseRelayStream` async iterator. Driver tasks are
    /// spawned lazily on the first `__anext__` (when the asyncio
    /// loop context is available) and aborted when the stream is
    /// dropped.
    fn stream(
        &self,
        py: Python<'_>,
        filter: Py<PyEventFilter>,
    ) -> PyResult<Py<PySseRelayStream>> {
        // 1. Resolve members + existing files synchronously (under
        // the GIL, before constructing the stream) so we don't hold
        // GIL across an await.
        let members = self.team_membership.borrow(py).members_rust();
        let storage_root = self.storage_root.clone();
        let buffer_size = self.buffer_size;

        // 2. Build one JsonlTailer per member whose file exists.
        //    The tailers are constructed with `since_event_seq=0`
        //    and `poll_interval_ms=0` (spin forever on EOF) — the
        //    relay is a live-tailer, not a one-shot reader.
        let mut tailers: Vec<(String, Py<PyJsonlTailer>)> = Vec::new();
        for m in &members {
            let path = storage_root.join(format!("events.{m}.jsonl"));
            if !path.exists() {
                continue;
            }
            let tailer = PyJsonlTailer::new_rust(path, 0, 0);
            tailers.push((m.clone(), Py::new(py, tailer)?));
        }

        // 3. Shared mpsc channel. Capacity is the relay's
        //    `buffer_size` (backpressure). Driver tasks send
        //    `(engine_id, json_line)`; the stream's `__anext__`
        //    receives.
        let (tx, rx) = mpsc::channel::<(String, String)>(buffer_size.max(1));

        // 4. Snapshot the filter so the stream owns its own copy.
        let filter_snapshot = filter.borrow(py).clone();

        // 5. Build the stream. Driver tasks are NOT spawned here;
        //    they are spawned lazily on first `__anext__` so they
        //    run inside the pyo3-async-runtimes tokio runtime that
        //    has the asyncio loop attached.
        Py::new(
            py,
            PySseRelayStream {
                tailers: StdMutex::new(Some(tailers)),
                tx,
                rx: Arc::new(tokio::sync::Mutex::new(rx)),
                filter: filter_snapshot,
                event_seq: Arc::new(std::sync::atomic::AtomicU64::new(0)),
                driver_tasks: StdMutex::new(None),
            },
        )
    }

    fn __repr__(&self) -> String {
        format!(
            "SseRelay(storage_root='{}', buffer_size={})",
            self.storage_root.display(),
            self.buffer_size
        )
    }
}