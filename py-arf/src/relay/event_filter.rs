//! Python `EventFilter` — predicate over `(engine_id, msg_type)`.
//!
//! Phase 7 / V1.x task 7. Used by `SseRelay` to drop events that don't
//! match the user's subscription. All filters are opt-in: an unset
//! `engine_ids` / `msg_types` allows everything; only when set, the
//! corresponding check is enforced.
//!
//! `since_event_seq` is stored per-engine so a reconnecting client can
//! resume a stream without re-receiving earlier events. The current
//! `matches()` does not consult it — applying the per-engine watermark
//! happens inside the relay once it picks up the actual `(node_id,
//! event_seq)` from the SSE line. We keep the field so callers can
//! pre-load it before handing the filter to the relay.

use std::collections::{HashMap, HashSet};

use pyo3::prelude::*;

/// Python `EventFilter` — predicate over `(engine_id, msg_type)`.
#[pyclass(name = "EventFilter")]
#[derive(Default, Clone)]
pub struct PyEventFilter {
    pub(crate) engine_ids: Option<HashSet<String>>,
    pub(crate) msg_types: Option<HashSet<String>>,
    pub(crate) since_event_seq: HashMap<String, u64>,
}

#[pymethods]
impl PyEventFilter {
    /// Construct an `EventFilter`.
    ///
    /// Args:
    ///   - `engine_ids`: optional set of allowed engine IDs; `None`
    ///     means "any engine".
    ///   - `msg_types`: optional set of allowed message types; `None`
    ///     means "any type".
    ///   - `since_event_seq`: optional per-engine resume cursor.
    #[new]
    #[pyo3(signature = (engine_ids=None, msg_types=None, since_event_seq=None))]
    fn new(
        engine_ids: Option<HashSet<String>>,
        msg_types: Option<HashSet<String>>,
        since_event_seq: Option<HashMap<String, u64>>,
    ) -> Self {
        Self {
            engine_ids,
            msg_types,
            since_event_seq: since_event_seq.unwrap_or_default(),
        }
    }

    /// Return `true` if `(engine_id, msg_type)` matches this filter.
    ///
    /// Both axes are checked independently: a missing `engine_ids`
    /// means "all engines pass" and similarly for `msg_types`.
    fn matches(&self, engine_id: &str, msg_type: &str) -> bool {
        if let Some(ids) = &self.engine_ids {
            if !ids.contains(engine_id) {
                return false;
            }
        }
        if let Some(types) = &self.msg_types {
            if !types.contains(msg_type) {
                return false;
            }
        }
        true
    }

    fn __repr__(&self) -> String {
        format!(
            "EventFilter(engine_ids={:?}, msg_types={:?}, since_event_seq={})",
            self.engine_ids,
            self.msg_types,
            self.since_event_seq.len()
        )
    }
}