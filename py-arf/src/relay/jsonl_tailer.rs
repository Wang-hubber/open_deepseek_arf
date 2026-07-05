//! Python `JsonlTailer` — async iterator over JSONL trace lines.
//!
//! Phase 7 / V1.x task 6: backing store for `SseRelay`, used to feed
//! live trace events from disk into an SSE stream.
//!
//! ## Implementation (Task 15)
//!
//! Replaces the previous one-shot synchronous-read placeholder with a
//! true async polling loop driven by `pyo3-async-runtimes`:
//!
//! - Opens the file lazily on the first `__anext__`.
//! - Holds a `BufReader` (with its internal byte cursor) inside an
//!   `Arc<tokio::sync::Mutex<_>>`, so subsequent `__anext__` calls
//!   resume exactly where the last one stopped — no re-reading from
//!   the start.
//! - On EOF, sleeps for `poll_interval_ms` (or yields to the runtime
//!   forever when `poll_interval_ms == 0`) and retries. This makes the
//!   tailer useful for streaming append-only JSONL files.
//! - `__aenter__` / `__aexit__` are exposed for explicit lifecycle
//!   control; on Drop the buffered reader is dropped which closes the
//!   underlying file handle.
//!
//! `since_event_seq` is retained on the struct for forward-compat with
//! line-based filtering (Task 17 may use it), but is currently a
//! no-op: the tailer emits every line it sees.

use std::path::PathBuf;
use std::sync::Arc;

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;
use tokio::sync::Mutex as TokioMutex;

/// Internal mutable state shared across async iterations.
struct JsonlTailerState {
    /// Path to the JSONL file (cloned into state for the open branch).
    path: PathBuf,
    /// Opened lazily on first `__anext__`. Holding a `BufReader` keeps
    /// the byte cursor and partial-line buffer alive across calls.
    reader: Option<std::io::BufReader<std::fs::File>>,
}

/// Python `JsonlTailer` — incrementally reads a JSONL trace file.
///
/// Wraps `Arc<tokio::sync::Mutex<JsonlTailerState>>` so `__anext__` can
/// hold `&self` (required by PyO3 for the async iterator protocol) while
/// still advancing the cursor between calls.
#[pyclass(name = "JsonlTailer")]
pub struct PyJsonlTailer {
    pub(crate) path: PathBuf,
    pub(crate) since_event_seq: u64,
    pub(crate) poll_interval_ms: u64,
    state: Arc<TokioMutex<JsonlTailerState>>,
}

#[pymethods]
impl PyJsonlTailer {
    /// Construct a tailer over `jsonl_path`.
    ///
    /// Args:
    ///   - `since_event_seq`: reserved — skip events with seq <= this
    ///     value (currently a no-op; the tailer emits every line).
    ///   - `poll_interval_ms`: how long to sleep between EOF-retry
    ///     attempts when the file has no new bytes. `0` means "spin
    ///     forever" (useful for tests that append promptly).
    #[new]
    fn new(jsonl_path: PathBuf, since_event_seq: u64, poll_interval_ms: u64) -> Self {
        let state = JsonlTailerState {
            path: jsonl_path.clone(),
            reader: None,
        };
        Self {
            path: jsonl_path,
            since_event_seq,
            poll_interval_ms,
            state: Arc::new(TokioMutex::new(state)),
        }
    }

    /// Async iterator protocol: returning `self` signals the iterator is ready.
    fn __aiter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    /// Async-context manager entry: returns `self` so the caller can use
    /// `async with JsonlTailer(...) as t:`.
    fn __aenter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    /// Async-context manager exit: drops the buffered reader (closing
    /// the underlying file handle). Idempotent.
    fn __aexit__<'py>(
        &self,
        py: Python<'py>,
        _exc_type: Py<PyAny>,
        _exc_val: Py<PyAny>,
        _exc_tb: Py<PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let state = self.state.clone();
        future_into_py(py, async move {
            let mut guard = state.lock().await;
            guard.reader = None;
            Ok(())
        })
    }

    /// Read the next line from the JSONL file.
    ///
    /// Returns a coroutine that:
    /// 1. Opens the file on first call (lazy).
    /// 2. Reads from the buffered cursor until a newline is found.
    /// 3. On EOF with no partial line, sleeps for `poll_interval_ms`
    ///    (or spins, if `0`) and retries — so an append-only JSONL
    ///    file keeps yielding new lines.
    /// 4. Returns the line content (with trailing `\n` stripped) as a
    ///    Python `str`. Raises `RuntimeError` on I/O failure.
    fn __anext__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let state = self.state.clone();
        let poll_interval = self.poll_interval_ms;
        let since_event_seq = self.since_event_seq;

        future_into_py(py, async move {
            next_line(&state, poll_interval, since_event_seq)
                .await
                .map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "JsonlTailer read error: {e}"
                    ))
                })
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "JsonlTailer(path='{}', since={}, poll_ms={})",
            self.path.display(),
            self.since_event_seq,
            self.poll_interval_ms
        )
    }
}

// Inherent impl for crate-internal construction. Lives outside
// `#[pymethods]` so the PyO3 macro does not try to wrap it as a
// Python method (which would mis-interpret `PathBuf`/`u64` parameters
// as PyO3 extractors).
impl PyJsonlTailer {
    /// Crate-internal constructor. Mirrors `new` but is callable from
    /// other modules in the `py_arf` crate (the `#[new]` attribute
    /// makes the Rust method private). Used by `SseRelay.stream` to
    /// build tailers per team member without going through Python.
    pub(crate) fn new_rust(
        jsonl_path: PathBuf,
        since_event_seq: u64,
        poll_interval_ms: u64,
    ) -> Self {
        let state = JsonlTailerState {
            path: jsonl_path.clone(),
            reader: None,
        };
        Self {
            path: jsonl_path,
            since_event_seq,
            poll_interval_ms,
            state: Arc::new(TokioMutex::new(state)),
        }
    }
}

/// Async polling core. Locks the shared state, opens the file on
/// first call, then loops reading lines until one complete line is
/// returned. EOF triggers a `tokio::time::sleep` so the tail stays
/// live; partial-line data without a trailing newline is accumulated
/// in the BufReader's internal buffer and resumed on the next pass.
async fn next_line(
    state: &Arc<TokioMutex<JsonlTailerState>>,
    poll_interval_ms: u64,
    _since_event_seq: u64,
) -> Result<String, std::io::Error> {
    use std::io::BufRead;

    // Lazily open the file under the lock, then drop the guard while
    // we sleep — we want concurrent `__aexit__` calls (which set
    // `reader = None`) to be able to make progress.
    {
        let mut guard = state.lock().await;
        if guard.reader.is_none() {
            let file = std::fs::File::open(&guard.path)?;
            guard.reader = Some(std::io::BufReader::new(file));
        }
    }

    loop {
        // Lock and try to read one line. We drop the guard before any
        // await so other tasks (e.g. `__aexit__` clearing the reader)
        // can make progress.
        let line_result = {
            let mut guard = state.lock().await;
            match guard.reader.as_mut() {
                Some(r) => {
                    let mut line = String::new();
                    let n = r.read_line(&mut line)?;
                    if n == 0 { None } else { Some(line) }
                }
                // `__aexit__` cleared the reader — behave like EOF and
                // loop back so a subsequent re-open could re-create it.
                None => None,
            }
        };

        match line_result {
            Some(line) => {
                return Ok(line.trim_end_matches(['\n', '\r']).to_string());
            }
            None => {
                // EOF — sleep then loop. Yield to the runtime even
                // when `poll_interval_ms == 0` so we don't pin a
                // worker thread.
                if poll_interval_ms == 0 {
                    tokio::task::yield_now().await;
                } else {
                    tokio::time::sleep(std::time::Duration::from_millis(poll_interval_ms)).await;
                }
            }
        }
    }
}