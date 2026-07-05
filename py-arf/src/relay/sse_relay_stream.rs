//! Python `SseRelayStream` — async iterator yielded by
//! `SseRelay.stream(filter)`.
//!
//! Phase 7 / V1.x task 16. The actual async iterator that aggregates
//! per-engine JSONL trace files (`<root>/events.<engine>.jsonl`) into a
//! merged stream of SSE-formatted chunks.
//!
//! ## Implementation
//!
//! Driver tasks are spawned lazily on the first `__anext__` call (rather
//! than in `SseRelay.stream`'s synchronous body). Reason:
//! `pyo3_async_runtimes::tokio::into_future` requires a running
//! Python asyncio event loop to schedule the awaitable. The asyncio
//! loop is bound to pyo3-async-runtimes' tokio runtime only inside
//! `future_into_py`/`local_future_into_py`. Calling `into_future`
//! outside that context fails with "no running event loop".
//!
//! Concretely:
//!
//! 1. `SseRelay.stream(filter)` (synchronous) builds a `PySseRelayStream`
//!    holding the list of `(engine_id, JsonlTailer)` pairs plus a
//!    shared `mpsc::Sender<(String, String)>` and `mpsc::Receiver`. No
//!    spawning happens yet.
//! 2. The first call to `__anext__` (running inside `future_into_py`,
//!    i.e. on pyo3-async-runtimes' tokio runtime with the asyncio
//!    loop attached) spawns one driver task per tailer via
//!    `pyo3_async_runtimes::tokio::get_runtime().spawn(...)`. Each
//!    driver loops calling `tailer.__anext__()` (a Python awaitable)
//!    via `into_future`, parses the line, and sends
//!    `(engine_id, json_line)` into the channel.
//! 3. `__anext__` then awaits the next message from the channel,
//!    applies the `EventFilter`, formats the line with
//!    `SseFormatter`, and returns one SSE chunk. Filtered events are
//!    silently dropped and the loop re-enters the channel `recv`.
//!
//! Subsequent `__anext__` calls reuse the already-spawned drivers.
//!
//! ## Send-ness
//!
//! `pyo3_async_runtimes::tokio` runs its futures on a multi-thread
//! tokio runtime with a Python asyncio loop attached. All our
//! spawned tasks use `pyo3_async_runtimes::tokio::get_runtime().spawn`
//! so they run on that runtime. Each driver calls `Python::attach`
//! once per `__anext__` to acquire the GIL and obtain the Python
//! awaitable, then drops the GIL across the await so other Python
//! threads can run.

use std::sync::Arc;
use std::sync::Mutex as StdMutex;

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::{future_into_py, into_future};
use serde_json::Value as JsonValue;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use super::event_filter::PyEventFilter;
use super::jsonl_tailer::PyJsonlTailer;
use super::sse_formatter::PySseFormatter;

/// Python `SseRelayStream` — async iterator merging N `JsonlTailer`s.
///
/// Constructed by `SseRelay.stream(filter)`. Each `__anext__` yields
/// one SSE-formatted string (`id: / event: / data:` triple) for the
/// next event that passes the filter.
#[pyclass(name = "SseRelayStream")]
pub struct PySseRelayStream {
    /// `(engine_id, JsonlTailer)` pairs to drive. Moved into the
    /// driver tasks on the first `__anext__`; `None` afterward.
    pub(crate) tailers: StdMutex<Option<Vec<(String, Py<PyJsonlTailer>)>>>,
    /// Sender end of the merged-channel. Driver tasks clone this.
    /// Held here so it isn't dropped until the stream itself is
    /// dropped (which would close the channel and signal drivers to
    /// stop).
    pub(crate) tx: mpsc::Sender<(String, String)>,
    /// Receiver end of the mpsc channel. Wrapped in
    /// `tokio::sync::Mutex` so the underlying `mpsc::Receiver` can be
    /// shared across `__anext__` calls.
    pub(crate) rx: Arc<tokio::sync::Mutex<mpsc::Receiver<(String, String)>>>,
    /// Snapshot of the filter at construction time.
    pub(crate) filter: PyEventFilter,
    /// Monotonic per-stream event sequence number, fed into the SSE
    /// `id:` field. Starts at 1 so `0` can remain a sentinel for
    /// "before any events".
    pub(crate) event_seq: Arc<std::sync::atomic::AtomicU64>,
    /// Holds the `JoinHandle`s for each tailer-driver task. Aborted
    /// when the stream is dropped.
    pub(crate) driver_tasks: StdMutex<Option<Vec<JoinHandle<()>>>>,
}

#[pymethods]
impl PySseRelayStream {
    /// Async iterator protocol: returning `self` signals the iterator is ready.
    fn __aiter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    /// Yield the next SSE chunk. On the first call, spawns the
    /// driver tasks (lazily, so they run inside the
    /// pyo3-async-runtimes tokio runtime that backs `future_into_py`).
    /// Subsequent calls reuse the spawned drivers.
    fn __anext__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        // 1. Lazy spawn on first call. We must be inside
        //    `future_into_py`'s runtime context (which has the asyncio
        //    loop attached) to use `into_future` from the spawned
        //    tasks.
        self.ensure_drivers_spawned(py)?;

        let rx = self.rx.clone();
        let filter = self.filter.clone();
        let event_seq = self.event_seq.clone();

        future_into_py(py, async move {
            loop {
                let (engine_id, line) = {
                    let mut guard = rx.lock().await;
                    match guard.recv().await {
                        Some(item) => item,
                        // All drivers ended (channel closed). For
                        // live tailers this shouldn't happen, but
                        // surface as StopAsyncIteration so a Python
                        // `async for` loop terminates cleanly.
                        None => {
                            return Err(
                                pyo3::exceptions::PyStopAsyncIteration::new_err(())
                            );
                        }
                    }
                };

                // Parse the JSON line so we can extract `event_type`
                // for the filter + SSE `event:` field.
                let parsed: JsonValue = match serde_json::from_str(&line) {
                    Ok(v) => v,
                    Err(_) => {
                        // Skip malformed lines silently — they may be
                        // partial writes, comments, or unrelated
                        // entries appended to the file.
                        continue;
                    }
                };

                let msg_type = parsed
                    .get("event_type")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();

                if !filter.matches_rust(&engine_id, &msg_type) {
                    continue;
                }

                let seq = event_seq.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
                let chunk = PySseFormatter::format_rust(&line, seq, &msg_type);
                return Ok(chunk);
            }
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "SseRelayStream(event_seq={})",
            self.event_seq
                .load(std::sync::atomic::Ordering::Relaxed)
        )
    }
}

impl Drop for PySseRelayStream {
    fn drop(&mut self) {
        // Drop the sender — this closes the channel and signals
        // drivers to stop their loops.
        // (self.tx is dropped implicitly when self is dropped.)
        // Abort any leftover driver tasks as a safety net.
        if let Ok(mut guard) = self.driver_tasks.lock() {
            if let Some(tasks) = guard.take() {
                for h in tasks {
                    h.abort();
                }
            }
        }
    }
}

impl PySseRelayStream {
    /// Spawn driver tasks on first call. Idempotent.
    ///
    /// Must be called from inside a `future_into_py` context (i.e.
    /// with pyo3-async-runtimes' tokio runtime active) so the
    /// spawned tasks can use `into_future` against the asyncio loop.
    ///
    /// The trick: each spawned task runs `pyo3_async_runtimes::tokio::scope`
    /// with the captured `TaskLocals` so the asyncio-loop context is
    /// visible to `into_future` calls inside the task.
    fn ensure_drivers_spawned(&self, py: Python<'_>) -> PyResult<()> {
        // Fast path: already spawned.
        {
            let guard = self.driver_tasks.lock().unwrap();
            if guard.is_some() {
                return Ok(());
            }
        }

        // Take the tailers list (so we move them into the spawned
        // tasks exactly once).
        let tailers = {
            let mut guard = self.tailers.lock().unwrap();
            guard.take()
        };
        let tailers = match tailers {
            Some(t) if !t.is_empty() => t,
            _ => {
                // No tailers — record an empty `Some(vec![])` so
                // future calls hit the fast path. The receiver will
                // block forever on `recv`, which is the correct
                // behavior for an empty relay (the consumer must
                // break out of its loop).
                let mut guard = self.driver_tasks.lock().unwrap();
                *guard = Some(Vec::new());
                return Ok(());
            }
        };

        // Capture the asyncio loop context so we can attach it to
        // each spawned task. Without this, `into_future` inside the
        // task fails with "no running event loop" because the
        // pyo3-async-runtimes `TASK_LOCALS` task-local is per-task
        // and doesn't propagate across `tokio::spawn` boundaries.
        let locals = pyo3_async_runtimes::tokio::get_current_locals(py)?;

        let mut handles = Vec::with_capacity(tailers.len());
        for (engine_id, tailer) in tailers {
            let tx = self.tx.clone();
            let engine_id_for_task = engine_id.clone();
            let locals_for_task = locals.clone();
            // Spawn on pyo3-async-runtimes' runtime, then enter
            // `scope(locals, ...)` so the asyncio-loop task-local is
            // set inside this task.
            let handle =
                pyo3_async_runtimes::tokio::get_runtime().spawn(async move {
                    pyo3_async_runtimes::tokio::scope(locals_for_task, async move {
                        loop {
                            // Acquire GIL, call __anext__, convert to a Send
                            // future that owns the Python awaitable.
                            let line_result: Result<String, pyo3::PyErr> = async {
                                let fut = Python::attach(|py| -> PyResult<_> {
                                    let bound = tailer
                                        .bind(py)
                                        .call_method0("__anext__")?;
                                    Ok(into_future(bound)?)
                                })?;
                                let line_py = fut.await?;
                                let line: String = Python::attach(|py| {
                                    line_py.extract(py)
                                })?;
                                Ok(line)
                            }
                            .await;

                            match line_result {
                                Ok(line) => {
                                    // If the receiver is gone (stream dropped),
                                    // stop the driver. `send` returns Err in that case.
                                    if tx
                                        .send((engine_id_for_task.clone(), line))
                                        .await
                                        .is_err()
                                    {
                                        break;
                                    }
                                }
                                Err(_e) => {
                                    // Tailer iteration exhausted or other
                                    // recoverable error — stop this driver. Live
                                    // tailers don't naturally end, so this is
                                    // effectively unreachable in practice.
                                    break;
                                }
                            }
                        }
                    })
                    .await
                });
            handles.push(handle);
        }

        let mut guard = self.driver_tasks.lock().unwrap();
        *guard = Some(handles);
        Ok(())
    }
}