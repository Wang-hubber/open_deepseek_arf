//! Python `JsonlTailer` — async iterator over JSONL trace lines.
//!
//! Phase 7 / V1.x task 6: backing store for `SseRelay`, used to feed
//! live trace events from disk into an SSE stream.
//!
//! ⚠️ The current `__anext__` is a simplified synchronous-read
//! implementation that reads exactly one line per await. The brief
//! explicitly notes this is a placeholder; production-grade async
//! polling with `pyo3-async-runtimes` is a follow-up task.

use std::path::PathBuf;

use pyo3::prelude::*;

/// Python `JsonlTailer` — incrementally reads a JSONL trace file.
#[pyclass(name = "JsonlTailer")]
pub struct PyJsonlTailer {
    pub(crate) path: PathBuf,
    pub(crate) since_event_seq: u64,
    pub(crate) poll_interval_ms: u64,
}

#[pymethods]
impl PyJsonlTailer {
    /// Construct a tailer over `jsonl_path`.
    ///
    /// Args:
    ///   - `since_event_seq`: skip events with seq <= this value
    ///   - `poll_interval_ms`: reserved for future async polling loop
    #[new]
    fn new(jsonl_path: PathBuf, since_event_seq: u64, poll_interval_ms: u64) -> Self {
        Self {
            path: jsonl_path,
            since_event_seq,
            poll_interval_ms,
        }
    }

    /// Async iterator protocol: returning `self` signals the iterator is ready.
    fn __aiter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    /// Read the next line from the JSONL file (one await per line in this
    /// placeholder implementation). On EOF returns an empty string; on
    /// I/O error raises `PyRuntimeError`.
    ///
    /// Implementation note: pyo3 0.29 dropped `Python::allow_threads(&self)`
    /// in favour of the consume-self `Python::detach(self)`. Since
    /// `Python<'py>` is `Copy`, calling `py.detach(...)` is fine even when
    /// we still need `py` afterwards for `PyString::new`.
    fn __anext__<'py>(slf: PyRefMut<'_, Self>, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let path = slf.path.clone();
        let line: String = py.detach(|| read_one_line_blocking(&path))?;
        Ok(pyo3::types::PyString::new(py, &line).into_any())
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

/// Synchronous-blocking helper that opens the file and reads a single
/// line. Used inside `py.detach(...)` so the file I/O happens outside
/// the GIL. Returns empty string on clean EOF.
fn read_one_line_blocking(path: &PathBuf) -> Result<String, PyErr> {
    use std::io::{BufRead, BufReader};
    let file = std::fs::File::open(path).map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("open jsonl {:?}: {e}", path))
    })?;
    let mut reader = BufReader::new(file);
    let mut line = String::new();
    let n = reader.read_line(&mut line).map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("read_line: {e}"))
    })?;
    if n == 0 {
        Ok(String::new())
    } else {
        Ok(line.trim().to_string())
    }
}
