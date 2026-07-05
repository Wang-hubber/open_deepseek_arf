//! Python `TeamMembership` — union of static YAML members and dynamic Bus members.
//!
//! Phase 7 / V1.x task 7. Reads `persistent_engines[].id` and
//! `subagent_pools[].id` from a `team.yaml` and exposes the union of
//! those IDs to the SSE relay. The `bus` argument is reserved for a
//! follow-up that subscribes to the Bus's `node_online` event and
//! merges live node IDs into the same set.
//!
//! Implementation note: `bus` is currently stored as `Option<PyObject>`
//! so tests can pass `None`. A real Bus wrapper would expose
//! `connected_node_ids()` which we would call at `members()` time.
//!
//! ⚠️ The current `members()` only returns the static YAML set. The
//! Bus-driven dynamic merge is a follow-up; the Python app side is
//! expected to subscribe to `node_online` and union the result with
//! this set in user-land.

use std::collections::HashSet;
use std::path::PathBuf;

use pyo3::prelude::*;

/// Python `TeamMembership` — static YAML members plus (eventually) live
/// Bus members.
#[pyclass(name = "TeamMembership")]
pub struct PyTeamMembership {
    pub(crate) static_members: HashSet<String>,
    /// Reserved for the dynamic Bus merge; `None` in the current
    /// skeleton. Holding it as `PyObject` lets a real `PyBus` be passed
    /// in once the bridge is implemented without changing the public
    /// constructor signature.
    pub(crate) bus: Option<Py<PyAny>>,
}

#[pymethods]
impl PyTeamMembership {
    /// Construct from a `team.yaml` and an optional `Bus`.
    ///
    /// Args:
    ///   - `team_config_path`: path to a YAML file with the schema
    ///     `{ team: { id }, persistent_engines: [{ id }], subagent_pools: [{ id }] }`.
    ///   - `bus`: optional `Bus` (current skeleton accepts `None`).
    ///
    /// Raises:
    ///   - `FileNotFoundError` if the YAML cannot be read.
    ///   - `ValueError` if the YAML cannot be parsed.
    #[new]
    fn new(team_config_path: PathBuf, bus: Option<Py<PyAny>>) -> PyResult<Self> {
        let content = std::fs::read_to_string(&team_config_path).map_err(|e| {
            pyo3::exceptions::PyFileNotFoundError::new_err(format!(
                "read team config {:?}: {e}",
                team_config_path
            ))
        })?;
        let v: serde_yaml::Value = serde_yaml::from_str(&content).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "parse team config {:?}: {e}",
                team_config_path
            ))
        })?;
        let mut members = HashSet::new();
        if let Some(arr) = v.get("persistent_engines").and_then(|x| x.as_sequence()) {
            for item in arr {
                if let Some(id) = item.get("id").and_then(|x| x.as_str()) {
                    members.insert(id.to_string());
                }
            }
        }
        if let Some(arr) = v.get("subagent_pools").and_then(|x| x.as_sequence()) {
            for item in arr {
                if let Some(id) = item.get("id").and_then(|x| x.as_str()) {
                    members.insert(id.to_string());
                }
            }
        }
        Ok(Self {
            static_members: members,
            bus,
        })
    }

    /// Return the static (YAML-driven) member set.
    ///
    /// Once the Bus bridge is wired in, this will union in the dynamic
    /// `node_online` set; today it returns only the YAML members.
    fn members(&self, py: Python) -> PyResult<HashSet<String>> {
        let _ = py; // reserved for future Bus call
        Ok(self.static_members.clone())
    }

    /// Crate-internal accessor used by `PySseRelay::stream` to avoid
    /// going back through Python for what is essentially a `HashSet`
    /// clone. The brief notes Bus-driven members will be added later;
    /// when they are, this accessor must merge them the same way
    /// `members()` does — keep them in sync.
    pub(crate) fn members_rust(&self) -> HashSet<String> {
        self.static_members.clone()
    }

    fn __repr__(&self) -> String {
        format!(
            "TeamMembership(static_members={})",
            self.static_members.len()
        )
    }
}