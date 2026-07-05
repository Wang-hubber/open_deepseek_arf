//! Python `SseFormatter` — Server-Sent-Event formatting helpers.
//!
//! Phase 7 / V1.x task 6: light-weight serialization used by
//! `SseRelay`. `format()` produces the standard SSE triple
//! (`id:` / `event:` / `data:`) followed by the empty-line terminator.
//! `parse_last_event_id()` is the inverse of the `id:` field when
//! encoded as `"{node_id}:{event_seq}"`.

use pyo3::prelude::*;

/// Python `SseFormatter` — stateless helpers, accessed via `#[staticmethod]`.
#[pyclass(name = "SseFormatter")]
pub struct PySseFormatter;

#[pymethods]
impl PySseFormatter {
    /// Format a single SSE event.
    ///
    /// Returns:
    ///   `"id: <event_seq>\nevent: <msg_type>\ndata: <event_json>\n\n"`
    #[staticmethod]
    fn format(event_json: &str, event_seq: u64, msg_type: &str) -> String {
        format!("id: {event_seq}\nevent: {msg_type}\ndata: {event_json}\n\n")
    }

    /// Parse the `id:` field's `"{node_id}:{event_seq}"` form back into
    /// `(node_id, event_seq)`. If `:` is missing, returns `(s, 0)`.
    #[staticmethod]
    fn parse_last_event_id(s: &str) -> (String, u64) {
        if let Some((id, seq)) = s.split_once(':') {
            (id.to_string(), seq.parse().unwrap_or(0))
        } else {
            (s.to_string(), 0)
        }
    }

    fn __repr__(&self) -> String {
        "SseFormatter".to_string()
    }
}
