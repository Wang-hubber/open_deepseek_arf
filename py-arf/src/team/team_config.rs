//! Python `TeamConfig` — declarative description of a team's structure.
//!
//! Phase 7 / V1.x task 8. A `TeamConfig` can be parsed from a
//! `team.yaml` (the on-disk format) or built programmatically with the
//! `new` / `add_persistent_engine` / `add_subagent_pool` helpers.
//! The two construction paths produce the same in-memory shape, so
//! downstream code (`TeamBuilder`, future agent factories) can consume
//! either one uniformly.
//!
//! Schema (subset that this binding reads):
//!
//! ```yaml
//! team:
//!   id: dev
//!   bus: shared
//! persistent_engines:
//!   - id: pm
//!     config: ./agents/pm.yaml
//!     auto_subscribe: [peer_message]
//! subagent_pools:
//!   - id: tc
//!     config: ./agents/tc.yaml
//!     size: 4
//!     max_queue_wait_ms: 2000
//! ```
//!
//! Notes:
//!   - `peer_topology` is intentionally not parsed here — it is a
//!     routing concern that the engine owns. It is still settable from
//!     Python via the `peer_topology` attribute for future use.
//!   - `to_yaml` writes a minimal round-tripable YAML. It is not a
//!     full pretty-printer; the goal is to give tests something they
//!     can grep for after a programmatic add.

use std::path::PathBuf;

use pyo3::prelude::*;

/// Python `EngineSpec` — one persistent engine in a team's roster.
///
/// Construct either via the `TeamConfig.add_persistent_engine()`
/// helper or directly with `EngineSpec(id, config_path, auto_subscribe)`.
#[pyclass(name = "EngineSpec")]
#[derive(Default, Clone, Debug)]
pub struct PyEngineSpec {
    #[pyo3(get, set)]
    pub engine_id: String,
    #[pyo3(get, set)]
    pub config_path: String,
    #[pyo3(get, set)]
    pub auto_subscribe: Vec<String>,
}

#[pymethods]
impl PyEngineSpec {
    #[new]
    #[pyo3(signature = (engine_id, config_path, auto_subscribe=vec![]))]
    fn new(engine_id: String, config_path: String, auto_subscribe: Vec<String>) -> Self {
        Self {
            engine_id,
            config_path,
            auto_subscribe,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "EngineSpec(engine_id='{}', config_path='{}', auto_subscribe={:?})",
            self.engine_id, self.config_path, self.auto_subscribe
        )
    }
}

/// Python `PoolSpec` — one bounded subagent pool in a team's roster.
#[pyclass(name = "PoolSpec")]
#[derive(Default, Clone, Debug)]
pub struct PyPoolSpec {
    #[pyo3(get, set)]
    pub pool_id: String,
    #[pyo3(get, set)]
    pub config_path: String,
    #[pyo3(get, set)]
    pub size: usize,
    #[pyo3(get, set)]
    pub max_queue_wait_ms: Option<u64>,
}

#[pymethods]
impl PyPoolSpec {
    #[new]
    #[pyo3(signature = (pool_id, config_path, size=1, max_queue_wait_ms=None))]
    fn new(
        pool_id: String,
        config_path: String,
        size: usize,
        max_queue_wait_ms: Option<u64>,
    ) -> Self {
        Self {
            pool_id,
            config_path,
            size,
            max_queue_wait_ms,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "PoolSpec(pool_id='{}', config_path='{}', size={}, max_queue_wait_ms={:?})",
            self.pool_id, self.config_path, self.size, self.max_queue_wait_ms
        )
    }
}

/// Python `TeamConfig` — declarative team description.
#[pyclass(name = "TeamConfig")]
#[derive(Default, Clone, Debug)]
pub struct PyTeamConfig {
    #[pyo3(get, set)]
    pub team_id: String,
    #[pyo3(get, set)]
    pub description: Option<String>,
    #[pyo3(get, set)]
    pub bus_id: String,
    #[pyo3(get, set)]
    pub persistent_engines: Vec<PyEngineSpec>,
    #[pyo3(get, set)]
    pub subagent_pools: Vec<PyPoolSpec>,
    /// Peer-topology is owned by the engine bus layer; the field is
    /// kept on the config for forward compatibility (follow-up tasks
    /// will consult it when wiring routes).
    #[pyo3(get, set)]
    pub peer_topology: Option<String>,
}

#[pymethods]
impl PyTeamConfig {
    /// Construct a minimal `TeamConfig` with just `team_id` and
    /// `bus_id`. Engines and pools are added via the `add_*` helpers.
    #[new]
    fn new(team_id: String, bus_id: String) -> Self {
        Self {
            team_id,
            bus_id,
            ..Default::default()
        }
    }

    /// Parse a `team.yaml` from disk.
    ///
    /// Reads `team.id`, `team.bus`, the full `persistent_engines[]`
    /// list (id / config / auto_subscribe), and the full
    /// `subagent_pools[]` list (id / config / size / max_queue_wait_ms).
    /// Unknown keys are silently ignored so the schema can grow
    /// without breaking older configs.
    ///
    /// Raises:
    ///   - `FileNotFoundError` if the path cannot be read.
    ///   - `ValueError` if the YAML cannot be parsed.
    #[staticmethod]
    fn from_yaml(path: PathBuf) -> PyResult<Self> {
        let s = std::fs::read_to_string(&path).map_err(|e| {
            pyo3::exceptions::PyFileNotFoundError::new_err(format!(
                "read team config {:?}: {e}",
                path
            ))
        })?;
        let v: serde_yaml::Value = serde_yaml::from_str(&s).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "parse team config {:?}: {e}",
                path
            ))
        })?;

        let team_section = v.get("team");
        let team_id = team_section
            .and_then(|t| t.get("id"))
            .and_then(|x| x.as_str())
            .unwrap_or("default")
            .to_string();
        let bus_id = team_section
            .and_then(|t| t.get("bus"))
            .and_then(|x| x.as_str())
            .unwrap_or("shared")
            .to_string();
        let description = team_section
            .and_then(|t| t.get("description"))
            .and_then(|x| x.as_str())
            .map(String::from);

        let mut engines = Vec::new();
        if let Some(arr) = v.get("persistent_engines").and_then(|x| x.as_sequence()) {
            for item in arr {
                engines.push(PyEngineSpec {
                    engine_id: item
                        .get("id")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string(),
                    config_path: item
                        .get("config")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string(),
                    auto_subscribe: item
                        .get("auto_subscribe")
                        .and_then(|x| x.as_sequence())
                        .map(|s| {
                            s.iter()
                                .filter_map(|v| v.as_str().map(String::from))
                                .collect()
                        })
                        .unwrap_or_default(),
                });
            }
        }

        let mut pools = Vec::new();
        if let Some(arr) = v.get("subagent_pools").and_then(|x| x.as_sequence()) {
            for item in arr {
                pools.push(PyPoolSpec {
                    pool_id: item
                        .get("id")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string(),
                    config_path: item
                        .get("config")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string(),
                    size: item.get("size").and_then(|x| x.as_u64()).unwrap_or(1) as usize,
                    max_queue_wait_ms: item.get("max_queue_wait_ms").and_then(|x| x.as_u64()),
                });
            }
        }

        Ok(Self {
            team_id,
            description,
            bus_id,
            persistent_engines: engines,
            subagent_pools: pools,
            peer_topology: None,
        })
    }

    /// Write a minimal round-tripable YAML representation to `path`.
    ///
    /// Output is hand-rolled (not `serde_yaml::to_string`) so it
    /// matches the schema documented above and tests can grep for the
    /// engine / pool IDs. Only `team_id`, `bus_id`, `persistent_engines[]`,
    /// and `subagent_pools[]` are emitted; `description` is dropped
    /// when `None` to keep the output minimal.
    fn to_yaml(&self, path: PathBuf) -> PyResult<()> {
        let mut out = String::new();
        out.push_str(&format!(
            "team:\n  id: {}\n  bus: {}\n",
            self.team_id, self.bus_id
        ));
        if !self.persistent_engines.is_empty() {
            out.push_str("\npersistent_engines:\n");
            for e in &self.persistent_engines {
                out.push_str(&format!(
                    "  - id: {}\n    config: {}\n",
                    e.engine_id, e.config_path
                ));
                if !e.auto_subscribe.is_empty() {
                    out.push_str("    auto_subscribe:\n");
                    for s in &e.auto_subscribe {
                        out.push_str(&format!("      - {s}\n"));
                    }
                }
            }
        }
        if !self.subagent_pools.is_empty() {
            out.push_str("\nsubagent_pools:\n");
            for p in &self.subagent_pools {
                out.push_str(&format!(
                    "  - id: {}\n    config: {}\n    size: {}\n",
                    p.pool_id, p.config_path, p.size
                ));
                if let Some(ms) = p.max_queue_wait_ms {
                    out.push_str(&format!("    max_queue_wait_ms: {ms}\n"));
                }
            }
        }
        std::fs::write(&path, out)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(format!("write {:?}: {e}", path)))?;
        Ok(())
    }

    /// Append a persistent engine to the roster.
    fn add_persistent_engine(
        &mut self,
        engine_id: &str,
        config_path: &str,
        auto_subscribe: Vec<String>,
    ) {
        self.persistent_engines.push(PyEngineSpec {
            engine_id: engine_id.to_string(),
            config_path: config_path.to_string(),
            auto_subscribe,
        });
    }

    /// Append a subagent pool to the roster.
    #[pyo3(signature = (pool_id, config_path, size=1, max_queue_wait_ms=None))]
    fn add_subagent_pool(
        &mut self,
        pool_id: &str,
        config_path: &str,
        size: usize,
        max_queue_wait_ms: Option<u64>,
    ) {
        self.subagent_pools.push(PyPoolSpec {
            pool_id: pool_id.to_string(),
            config_path: config_path.to_string(),
            size,
            max_queue_wait_ms,
        });
    }

    fn __repr__(&self) -> String {
        format!(
            "TeamConfig(team_id='{}', bus_id='{}', persistent_engines={}, subagent_pools={})",
            self.team_id,
            self.bus_id,
            self.persistent_engines.len(),
            self.subagent_pools.len()
        )
    }
}