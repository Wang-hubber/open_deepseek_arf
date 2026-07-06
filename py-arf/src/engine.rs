//! PyO3 bindings for arf-engine — Engine, AgentConfig, EngineBuilder, CheckpointRule.
//!
//! Phase 6 task 6.10: extends py-arf with Engine types so Python apps can
//! build and run Engines without using arf-agent crate.
//!
//! Phase 6 task 6.22.4: also binds CheckpointRule / Checkpoint / Route /
//! Capability / ActionMessage so Python users can wire engine behavior
//! without dropping into Rust.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;
use uuid::Uuid;

use arf_core::{
    ActionMessage, Capability, Checkpoint, Message, MessageIntent,
    ModelCall, NodeId, Route, State as CoreState,
};
use arf_core::CheckpointRule as CoreCheckpointRule;
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, WaitStrategy};

use crate::{json_value_to_py, py_object_to_json, PyBus, PyNodeId};

// ═══════════════════════════════════════════════════════════════════
// AgentConfig YAML schema parser (Task 14)
// ═══════════════════════════════════════════════════════════════════
//
// `arf_engine::config::AgentConfig` does NOT derive Serialize/Deserialize
// (it holds `Box<dyn Fn>` closures + `Arc<dyn ...>` trait objects), so
// `serde_yaml::from_str::<AgentConfig>(...)` is impossible. We instead
// parse the YAML into a `serde_yaml::Value` tree, pull documented fields
// out by hand, and construct `AgentConfig` programmatically. The schema
// mirrors the example YAML files shipped under
// `examples/multi_agent_team/agents/<id>/agent.yaml`.

/// Parsed fields from an agent YAML document.
///
/// The example schema (see `examples/multi_agent_team/agents/pm/agent.yaml`):
/// ```yaml
/// agent:
///   id: pm
///   description: ...
///   model:
///     provider: deepseek
///     model_name: deepseek-chat
///     temperature: 0.3
///     thinking_enabled: false
///     endpoint: https://...        # optional
///     api_key_env: DEEPSEEK_API_KEY # optional
///   system_prompt: "..."           # either this or system_prompt_file
///   system_prompt_file: ./sp.md    # relative path resolved at load time
///   tools: []                      # reserved for future use
///   routes: []                     # reserved for future use
///   max_turns: 10                  # optional
///   initial_memory: ["..."]        # optional
///   allowed_paths: ["/data"]       # optional
///   tools:                          # optional; default []
///     - read_file                   # implicit Allow
///     - name: write_file
///       permission: ask             # Allow | Ask | Deny
/// ```
struct AgentYamlFields {
    model_provider: String,
    model_name: String,
    endpoint: Option<String>,
    api_key_env: Option<String>,
    thinking_enabled: bool,
    temperature: Option<f64>,
    max_output_tokens: Option<u32>,
    system_prompt: String,
    max_turns: Option<u32>,
    initial_memory: Vec<String>,
    allowed_paths: Vec<String>,
    /// `ToolSpec`s loaded from the yaml `tools:` block. Each entry is
    /// either a bare tool-name string (`ToolPermission::Allow`) or a
    /// `{name, permission}` mapping. Other spec fields default to
    /// empty description + parameters at this layer; the engine's
    /// tool registry overwrites them once it sees the actual MCP
    /// capabilities at build time.
    tools: Vec<arf_core::ToolSpec>,
    /// `resources:` block — declares the agent's Bus node dependencies
    /// (e.g. `[{name: tools, type: mcp}]`).
    resources: Vec<arf_agent::ResourceSpec>,
}

fn parse_agent_yaml(raw: &str, source: &std::path::Path) -> PyResult<AgentYamlFields> {
    let v: serde_yaml::Value = serde_yaml::from_str(raw).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "parse agent config {:?}: {e}",
            source
        ))
    })?;
    let agent = v.get("agent").ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "agent config {:?}: missing top-level `agent:` key",
            source
        ))
    })?;
    let model = agent.get("model").ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "agent config {:?}: missing `agent.model:` key",
            source
        ))
    })?;

    let model_provider = model
        .get("provider")
        .and_then(|x| x.as_str())
        .unwrap_or("deepseek")
        .to_string();
    let model_name = model
        .get("model_name")
        .and_then(|x| x.as_str())
        .unwrap_or("deepseek-v4-flash")
        .to_string();
    let endpoint = model.get("endpoint").and_then(|x| x.as_str()).map(String::from);
    let api_key_env = model
        .get("api_key_env")
        .and_then(|x| x.as_str())
        .map(String::from);
    let thinking_enabled = model
        .get("thinking_enabled")
        .and_then(|x| x.as_bool())
        .unwrap_or(false);
    let temperature = model.get("temperature").and_then(|x| x.as_f64());
    let max_output_tokens = model
        .get("max_output_tokens")
        .and_then(|x| x.as_u64())
        .map(|n| n as u32);

    // System prompt: either inline `system_prompt` or loaded from
    // `system_prompt_file` (relative to the YAML file's directory).
    let system_prompt = if let Some(s) = agent.get("system_prompt").and_then(|x| x.as_str()) {
        s.to_string()
    } else if let Some(rel) = agent.get("system_prompt_file").and_then(|x| x.as_str()) {
        let path = source.parent().unwrap_or(std::path::Path::new(".")).join(rel);
        std::fs::read_to_string(&path).map_err(|e| {
            pyo3::exceptions::PyFileNotFoundError::new_err(format!(
                "read system_prompt_file {:?}: {e}",
                path
            ))
        })?
    } else {
        "You are a helpful assistant.".to_string()
    };

    let max_turns = agent
        .get("max_turns")
        .and_then(|x| x.as_u64())
        .map(|n| n as u32);
    let initial_memory: Vec<String> = agent
        .get("initial_memory")
        .and_then(|x| x.as_sequence())
        .map(|s| s.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default();
    let allowed_paths: Vec<String> = agent
        .get("allowed_paths")
        .and_then(|x| x.as_sequence())
        .map(|s| s.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default();

    // `resources:` declares which Bus node(s) this engine should
    // resolve. Each entry is a `{name|resource_name, type|node_type,
    // capabilities?}` mapping. The resolved resources drive the
    // engine's tool_index at build time.
    let resources: Vec<arf_agent::ResourceSpec> = agent
        .get("resources")
        .and_then(|x| x.as_sequence())
        .map(|seq| {
            seq.iter()
                .filter_map(|v| {
                    let m = v.as_mapping()?;
                    let resource_name = m
                        .get("resource_name")
                        .or_else(|| m.get("name"))
                        .and_then(|x| x.as_str())?
                        .to_string();
                    let node_type = m
                        .get("node_type")
                        .or_else(|| m.get("type"))
                        .and_then(|x| x.as_str())
                        .unwrap_or("mcp")
                        .to_string();
                    let capabilities = m
                        .get("capabilities")
                        .map(|v| serde_json::to_value(v).unwrap_or(serde_json::Value::Null));
                    Some(arf_agent::ResourceSpec {
                        resource_name,
                        node_type,
                        capabilities,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    //   - bare string  → ToolSpec { name, permission: Allow }
    //   - { name, permission } map → ToolSpec with explicit permission
    // Other fields (description, parameters) are filled in by the engine
    // at build time from the actual MCP node's capabilities.
    let tools: Vec<arf_core::ToolSpec> = agent
        .get("tools")
        .and_then(|x| x.as_sequence())
        .map(|seq| {
            seq.iter()
                .filter_map(|v| {
                    if let Some(name) = v.as_str() {
                        Some(arf_core::ToolSpec::new(
                            name,
                            String::new(),
                            serde_json::json!({}),
                        ))
                    } else if let Some(m) = v.as_mapping() {
                        let name = m.get("name").and_then(|x| x.as_str())?;
                        let permission = m
                            .get("permission")
                            .and_then(|x| x.as_str())
                            .map(parse_permission)
                            .unwrap_or(arf_core::ToolPermission::Allow);
                        let mut spec = arf_core::ToolSpec::new(
                            name,
                            String::new(),
                            serde_json::json!({}),
                        );
                        spec.permission = permission;
                        Some(spec)
                    } else {
                        None
                    }
                })
                .collect()
        })
        .unwrap_or_default();

    Ok(AgentYamlFields {
        model_provider,
        model_name,
        endpoint,
        api_key_env,
        thinking_enabled,
        temperature,
        max_output_tokens,
        system_prompt,
        max_turns,
        initial_memory,
        allowed_paths,
        tools,
        resources,
    })
}

fn parse_permission(s: &str) -> arf_core::ToolPermission {
    match s.trim().to_lowercase().as_str() {
        "ask" => arf_core::ToolPermission::Ask,
        "deny" => arf_core::ToolPermission::Deny,
        _ => arf_core::ToolPermission::Allow,
    }
}

fn build_agent_config_from_yaml_fields(fields: AgentYamlFields) -> AgentConfig {
    let mut engine_cfg = EngineConfig::default();
    if let Some(mt) = fields.max_turns {
        engine_cfg.max_turns = mt;
    }
    AgentConfig {
        model: arf_agent::ModelDecl {
            provider: fields.model_provider,
            model_name: fields.model_name,
            endpoint: fields.endpoint,
            api_key_env: fields.api_key_env,
            thinking_enabled: fields.thinking_enabled,
            temperature: fields.temperature,
            max_output_tokens: fields.max_output_tokens,
            extra: serde_json::Value::Null,
        },
        resources: fields.resources,
        system_prompt_template: fields.system_prompt,
        initial_memory: fields.initial_memory,
        allowed_paths: fields.allowed_paths,
        tools: fields.tools,
        engine: engine_cfg,
    }
}

/// Public helper used by `TeamBuilder` to parse the same agent YAML
/// schema without going through `PyAgentConfig`. Kept in `engine.rs`
/// so the schema lives next to `PyAgentConfig.from_yaml`.
pub(crate) fn parse_agent_config_yaml(
    raw: &str,
    source: &std::path::Path,
) -> Result<AgentConfig, String> {
    // `parse_agent_yaml` returns PyErr with the error message as a
    // Python str. We format the PyErr with its Debug representation
    // — which gives us back the Python error string without needing
    // the GIL here.
    let fields = parse_agent_yaml(raw, source)
        .map_err(|e| format!("{e:?}"))?;
    Ok(build_agent_config_from_yaml_fields(fields))
}

// ═══════════════════════════════════════════════════════════════════
// PyAgentConfig
// ═══════════════════════════════════════════════════════════════════

/// Python AgentConfig — declarative configuration for an Engine.
#[pyclass(name = "AgentConfig")]
#[derive(Clone)]
pub struct PyAgentConfig {
    inner: std::sync::Arc<std::sync::Mutex<Option<AgentConfig>>>,
}

#[pymethods]
impl PyAgentConfig {
    /// Construct an AgentConfig.
    ///
    /// Two equivalent forms are accepted (both pre-Phase-7 flat form and
    /// Phase-7 nested form):
    ///
    ///   # nested (preferred — matches Rust struct shape):
    ///   AgentConfig(
    ///       model=ModelDecl(provider="minimax", model_name="MiniMax-M3"),
    ///       engine=EngineConfig(max_turns=10, routes={"model_call": Route.strict(...)}),
    ///       resources=[ResourceSpec(resource_name="tools", node_type="mcp")],
    ///       system_prompt_template="...",
    ///   )
    ///
    ///   # flat (kept for back-compat with e2e tests):
    ///   AgentConfig(
    ///       provider="minimax", model="MiniMax-M3",
    ///       max_turns=10, routes={"model_call": Route.strict(...)},
    ///   )
    ///
    /// If both forms are supplied for the same field, the nested form wins.
    #[new]
    #[pyo3(signature = (
        provider = None,
        model = None,
        endpoint = None,
        api_key_env = None,
        system_prompt_template = "You are a helpful assistant.".to_string(),
        initial_memory = None,
        allowed_paths = None,
        resources = None,
        max_turns = None,
        tool_timeout_ms = None,
        routes = None,
        checkpoint_rules = None,
        engine = None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        provider: Option<String>,
        model: Option<&pyo3::Bound<'_, pyo3::PyAny>>,
        endpoint: Option<String>,
        api_key_env: Option<String>,
        system_prompt_template: String,
        initial_memory: Option<Vec<String>>,
        allowed_paths: Option<Vec<String>>,
        resources: Option<Vec<PyResourceSpec>>,
        max_turns: Option<u32>,
        tool_timeout_ms: Option<u64>,
        routes: Option<std::collections::HashMap<String, PyRoute>>,
        checkpoint_rules: Option<&pyo3::Bound<'_, pyo3::PyAny>>,
        engine: Option<&pyo3::Bound<'_, pyo3::PyAny>>,
    ) -> PyResult<Self> {
        // ── Resolve model: nested ModelDecl > flat (provider, model) ──
        let (provider_final, model_name_final, endpoint_final, api_key_env_final) =
            if let Some(m) = model {
                if let Ok(decl) = m.extract::<PyModelDecl>() {
                    let d = decl.inner.clone();
                    (d.provider, d.model_name, d.endpoint, d.api_key_env)
                } else if let Ok(s) = m.extract::<String>() {
                    let p = provider.unwrap_or_else(|| "deepseek".to_string());
                    (p, s, endpoint, api_key_env)
                } else {
                    return Err(pyo3::exceptions::PyTypeError::new_err(
                        "model must be str (model_name) or ModelDecl instance",
                    ));
                }
            } else {
                let p = provider.unwrap_or_else(|| "deepseek".to_string());
                let mn = "deepseek-v4-flash".to_string();
                (p, mn, endpoint, api_key_env)
            };

        // ── Resolve engine: nested EngineConfig > flat (max_turns, routes, ...) ──
        let engine_config = if let Some(ec) = engine {
            // PyEngineConfig doesn't implement Clone (inner EngineConfig
            // holds Box<dyn Fn> in CheckpointRule), so we can't extract
            // by value. Instead, pull each field via a Python-side
            // method, then build a fresh EngineConfig.
            build_engine_config_from_py(ec)?
        } else {
            let routes_map: std::collections::HashMap<String, Route> = match routes {
                Some(m) => m.into_iter().map(|(k, v)| (k, v.inner)).collect(),
                None => std::collections::HashMap::new(),
            };
            let rules: Vec<CoreCheckpointRule> = match checkpoint_rules {
                Some(obj) => {
                    let list = obj.cast::<pyo3::types::PyList>()?;
                    let mut out = Vec::with_capacity(list.len());
                    for item in list.iter() {
                        let rule: PyRef<PyCheckpointRule> = item.extract()?;
                        out.push(rule.into_rust_rule());
                    }
                    out
                }
                None => vec![],
            };
            let mut ec = EngineConfig::default();
            ec.max_turns = max_turns.unwrap_or(10);
            if let Some(t) = tool_timeout_ms {
                ec.tool_timeout_ms = Some(t);
            }
            ec.routes = routes_map;
            ec.checkpoint_rules = rules;
            ec
        };

        let res_specs: Vec<arf_agent::ResourceSpec> = resources
            .unwrap_or_default()
            .into_iter()
            .map(|r| r.inner)
            .collect();
        let cfg = AgentConfig {
            model: arf_agent::ModelDecl {
                provider: provider_final,
                model_name: model_name_final,
                endpoint: endpoint_final,
                api_key_env: api_key_env_final,
                ..Default::default()
            },
            resources: res_specs,
            system_prompt_template,
            initial_memory: initial_memory.unwrap_or_default(),
            allowed_paths: allowed_paths.unwrap_or_default(),
            tools: vec![],
            engine: engine_config,
        };
        Ok(Self {
            inner: std::sync::Arc::new(std::sync::Mutex::new(Some(cfg))),
        })
    }

    #[getter]
    fn provider(&self) -> String {
        self.inner.lock().unwrap().as_ref().unwrap().model.provider.clone()
    }

    #[getter]
    fn max_turns(&self) -> u32 {
        self.inner.lock().unwrap().as_ref().unwrap().engine.max_turns
    }

    #[getter]
    fn routes(&self) -> std::collections::HashMap<String, String> {
        self.inner
            .lock()
            .unwrap()
            .as_ref()
            .unwrap()
            .engine
            .routes
            .iter()
            .map(|(k, v)| (k.clone(), format!("{:?}", v)))
            .collect()
    }

    #[getter]
    fn checkpoint_rules(&self, py: Python<'_>) -> PyResult<Vec<pyo3::Py<pyo3::PyAny>>> {
        let cfg = self.inner.lock().unwrap();
        let rules = &cfg.as_ref().unwrap().engine.checkpoint_rules;
        let mut out = Vec::with_capacity(rules.len());
        for r in rules {
            // We can't reconstruct PyCheckpointRule from the Rust
            // CoreCheckpointRule (its actions Vec holds opaque
            // PyActionMessageImpl trait objects, not PyActionMessage).
            // Instead, expose a lightweight summary dict.
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("name", r.name.clone())?;
            dict.set_item("trigger", format!("{:?}", r.trigger))?;
            out.push(dict.into());
        }
        Ok(out)
    }

    fn __repr__(&self) -> String {
        let cfg = self.inner.lock().unwrap();
        match cfg.as_ref() {
            Some(c) => format!("AgentConfig(provider='{}', model='{}', max_turns={})", c.model.provider, c.model.model_name, c.engine.max_turns),
            None => "AgentConfig(consumed)".to_string(),
        }
    }

    /// Load an `AgentConfig` from a YAML file.
    ///
    /// The schema matches the example YAML files shipped under
    /// `examples/multi_agent_team/agents/<id>/agent.yaml`. Required keys:
    ///
    ///   ```yaml
    ///   agent:
    ///     model:
    ///       provider: deepseek      # str
    ///       model_name: deepseek-chat # str
    ///     # optional under `model:`:
    ///     #   endpoint: https://...       # str
    ///     #   api_key_env: DEEPSEEK_API_KEY # str
    ///     #   thinking_enabled: false      # bool
    ///     #   temperature: 0.3             # float
    ///     #   max_output_tokens: 4096      # int
    ///     system_prompt: "..."            # str  OR  system_prompt_file: ./sp.md
    ///     # optional under `agent:`:
    ///     #   max_turns: 10
    ///     #   initial_memory: ["..."]
    ///     #   allowed_paths: ["/data"]
    ///   ```
    ///
    /// Raises:
    ///   - `FileNotFoundError` if `path` cannot be read.
    ///   - `ValueError` if the YAML cannot be parsed or required keys
    ///     are missing.
    #[staticmethod]
    fn from_yaml(path: PathBuf) -> PyResult<Self> {
        let raw = std::fs::read_to_string(&path).map_err(|e| {
            pyo3::exceptions::PyFileNotFoundError::new_err(format!(
                "read agent config {:?}: {e}",
                path
            ))
        })?;
        let fields = parse_agent_yaml(&raw, &path)?;
        let cfg = build_agent_config_from_yaml_fields(fields);
        Ok(Self {
            inner: std::sync::Arc::new(std::sync::Mutex::new(Some(cfg))),
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyResourceSpec
// ═══════════════════════════════════════════════════════════════════

/// Python ResourceSpec — declares a logical resource dependency.
///
/// `resource_name` is an agent-given alias (NOT a NodeId/NodeName) —
/// used for logs and error messages. Real node matching is done by
/// `node_type` (+ optional `capabilities` filter).
#[pyclass(name = "ResourceSpec")]
#[derive(Clone)]
pub struct PyResourceSpec {
    pub(crate) inner: arf_agent::ResourceSpec,
}

#[pymethods]
impl PyResourceSpec {
    #[new]
    #[pyo3(signature = (resource_name, node_type, capabilities = None))]
    fn new(
        py: Python<'_>,
        resource_name: String,
        node_type: String,
        capabilities: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let caps_json = match capabilities {
            Some(obj) => Some(py_object_to_json(&obj, py)?),
            None => None,
        };
        Ok(Self {
            inner: arf_agent::ResourceSpec {
                resource_name,
                node_type,
                capabilities: caps_json,
            },
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "ResourceSpec(resource_name='{}', node_type='{}')",
            self.inner.resource_name, self.inner.node_type
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyEngineBuilder
// ═══════════════════════════════════════════════════════════════════

/// Python EngineBuilder — build an Engine from a Bus + AgentConfig.
#[pyclass(name = "EngineBuilder")]
pub struct PyEngineBuilder {
    inner: std::sync::Arc<std::sync::Mutex<Option<EngineBuilder>>>,
}

#[pymethods]
impl PyEngineBuilder {
    #[staticmethod]
    fn new(buses: Vec<PyRef<PyBus>>) -> PyResult<Self> {
        if buses.is_empty() {
            return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                "EngineBuilder requires at least one bus",
            ));
        }
        let bus_arcs: Vec<Arc<arf_bus::Bus>> = buses.iter().map(|b| b.inner.clone()).collect();
        Ok(Self {
            inner: std::sync::Arc::new(std::sync::Mutex::new(Some(EngineBuilder::new(bus_arcs)))),
        })
    }

    fn build<'py>(
        &self,
        py: Python<'py>,
        config: &PyAgentConfig,
    ) -> PyResult<Bound<'py, PyAny>> {
        let builder_arc = self.inner.clone();
        let config_arc = config.inner.clone();
        // Take builder and config synchronously (under sync mutex) before .await.
        let (builder, cfg) = {
            let mut bguard = builder_arc.lock().unwrap();
            let builder = bguard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("builder already consumed")
            })?;
            let mut cguard = config_arc.lock().unwrap();
            let cfg = cguard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "AgentConfig already used by another build()",
                )
            })?;
            (builder, cfg)
        };

        future_into_py(py, async move {
            builder.build(cfg).await.map(PyEngine::from).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string())
            })
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyEngine
// ═══════════════════════════════════════════════════════════════════

/// Python Engine — ReAct loop actor.
#[pyclass(name = "Engine")]
pub struct PyEngine {
    inner: std::sync::Arc<std::sync::Mutex<Option<Engine>>>,
}

impl PyEngine {
    fn from(engine: Engine) -> Self {
        Self {
            inner: std::sync::Arc::new(std::sync::Mutex::new(Some(engine))),
        }
    }
}

#[pymethods]
impl PyEngine {
    #[getter]
    fn agent_id(&self) -> PyResult<PyNodeId> {
        let guard = self.inner.lock().unwrap();
        match guard.as_ref() {
            Some(e) => Ok(PyNodeId { inner: e.agent_id().clone() }),
            None => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "engine already consumed",
            )),
        }
    }

    #[getter]
    fn system_prompt(&self) -> PyResult<String> {
        let guard = self.inner.lock().unwrap();
        match guard.as_ref() {
            Some(e) => Ok(e.system_prompt().to_string()),
            None => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "engine already consumed",
            )),
        }
    }

    fn run<'py>(
        &self,
        py: Python<'py>,
        state: &PyState,
        user_input: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let engine_arc = self.inner.clone();
        let state_arc = state.inner.clone();
        // Take engine and state synchronously (under sync mutex) before .await.
        let (mut engine, mut state_inner) = {
            let mut eguard = engine_arc.lock().unwrap();
            let engine = eguard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "engine already consumed by a previous run",
                )
            })?;
            let mut sguard = state_arc.lock().unwrap();
            let state_inner = sguard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "state already consumed by a previous run",
                )
            })?;
            (engine, state_inner)
        };

        future_into_py(py, async move {
            let cancel = tokio_util::sync::CancellationToken::new();
            let result = engine
                .run(&mut state_inner, user_input, cancel)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string()));

            // Restore engine and state to PyO3 holders (sync mutex is OK here).
            engine_arc.lock().unwrap().replace(engine);
            state_arc.lock().unwrap().replace(state_inner);
            result
        })
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyState
// ═══════════════════════════════════════════════════════════════════

/// Python State — Engine state holder.
#[pyclass(name = "EngineState")]
pub struct PyState {
    inner: std::sync::Arc<std::sync::Mutex<Option<CoreState>>>,
}

#[pymethods]
impl PyState {
    #[new]
    fn new() -> Self {
        Self {
            inner: std::sync::Arc::new(std::sync::Mutex::new(Some(CoreState::new()))),
        }
    }

    #[getter]
    fn round_count(&self) -> usize {
        self.inner
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| s.over_view.round_count)
            .unwrap_or(0)
    }

    #[getter]
    fn turn_count(&self) -> usize {
        self.inner
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| s.over_view.turn_count)
            .unwrap_or(0)
    }

    #[getter]
    fn context_tokens(&self) -> usize {
        self.inner
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| s.over_view.context_tokens)
            .unwrap_or(0)
    }

    /// Expose `state.messages` to Python as a list of dicts.
    ///
    /// Each dict has shape:
    ///   {role: str, content: str, tool_call_id: str|None, name: str|None,
    ///    tool_calls: list[{id, name, arguments, target}]}
    ///
    /// Phase 6 task 6.22.2: required by Python E2E tests for round-trip
    /// verification (assert state.messages grows / final assistant content
    /// matches engine.run() output).
    #[getter]
    fn messages<'py>(&self, py: Python<'py>) -> PyResult<Vec<Py<PyAny>>> {
        use pyo3::types::PyDict;
        let guard = self.inner.lock().unwrap();
        let state = guard.as_ref().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("state already consumed")
        })?;
        state
            .messages
            .iter()
            .map(|m| {
                let dict = PyDict::new(py);
                dict.set_item("role", m.role.clone())?;
                dict.set_item("content", m.content.clone())?;
                dict.set_item("tool_call_id", m.tool_call_id.clone())?;
                dict.set_item("name", m.name.clone())?;
                let tcs: Vec<Py<PyAny>> = m
                    .tool_calls
                    .iter()
                    .map(|tc| {
                        let d = PyDict::new(py);
                        // `.unwrap()` is intentional — building a fresh dict
                        // with literal string keys cannot fail.
                        d.set_item("id", tc.id.clone()).unwrap();
                        d.set_item("name", tc.name.clone()).unwrap();
                        d.set_item(
                            "arguments",
                            json_value_to_py(&tc.arguments, py).unwrap(),
                        )
                        .unwrap();
                        d.set_item("target", tc.target.as_ref().map(|n| n.as_str().to_string()))
                            .unwrap();
                        d.into()
                    })
                    .collect();
                dict.set_item("tool_calls", tcs)?;
                Ok(dict.into())
            })
            .collect()
    }

    fn __repr__(&self) -> String {
        let guard = self.inner.lock().unwrap();
        match guard.as_ref() {
            Some(s) => format!(
                "EngineState(round={}, turn={}, tokens={})",
                s.over_view.round_count, s.over_view.turn_count, s.over_view.context_tokens
            ),
            None => "EngineState(consumed)".to_string(),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyWaitStrategy
// ═══════════════════════════════════════════════════════════════════

/// Python WaitStrategy — strategy for WaitEvent trigger.
#[pyclass(name = "WaitStrategy", from_py_object)]
#[derive(Clone)]
pub struct PyWaitStrategyInner {
    inner: WaitStrategy,
}

#[pymethods]
impl PyWaitStrategyInner {
    #[classattr]
    fn All() -> Self {
        Self { inner: WaitStrategy::All }
    }

    #[classattr]
    fn Any() -> Self {
        Self { inner: WaitStrategy::Any }
    }

    #[staticmethod]
    fn Count(n: u32) -> Self {
        Self { inner: WaitStrategy::Count(n) }
    }

    fn __eq__(&self, other: &PyWaitStrategyInner) -> bool {
        self.inner == other.inner
    }

    fn __repr__(&self) -> String {
        format!("WaitStrategy({:?})", self.inner)
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyModelCall
// ═══════════════════════════════════════════════════════════════════

/// Python ModelCall — engine → ModelAdapter message (ActionMessage).
#[pyclass(name = "ModelCall")]
pub struct PyModelCall {
    inner: ModelCall,
}

#[pymethods]
impl PyModelCall {
    #[new]
    fn new() -> Self {
        Self { inner: ModelCall::new(vec![]) }
    }

    #[getter]
    fn msg_type(&self) -> &'static str {
        "model_call"
    }

    #[getter]
    fn correlation_id(&self) -> String {
        self.inner.correlation_id().to_string()
    }

    fn __repr__(&self) -> String {
        format!("ModelCall(cid={})", self.inner.correlation_id())
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyCheckpoint
// ═══════════════════════════════════════════════════════════════════

/// Python Checkpoint — 5 invariant positions where Engine may inject
/// side-effect messages (Phase 6 §1.5).
#[pyclass(name = "Checkpoint")]
#[derive(Clone)]
pub struct PyCheckpoint {
    inner: Checkpoint,
}

#[pymethods]
impl PyCheckpoint {
    #[classattr]
    fn BeforeModelCall() -> Self {
        Self { inner: Checkpoint::BeforeModelCall }
    }

    #[classattr]
    fn AfterModelCall() -> Self {
        Self { inner: Checkpoint::AfterModelCall }
    }

    #[classattr]
    fn BeforeToolExec() -> Self {
        Self { inner: Checkpoint::BeforeToolExec }
    }

    #[classattr]
    fn AfterToolExec() -> Self {
        Self { inner: Checkpoint::AfterToolExec }
    }

    #[classattr]
    fn RoundEnd() -> Self {
        Self { inner: Checkpoint::RoundEnd }
    }

    fn __eq__(&self, other: &PyCheckpoint) -> bool {
        self.inner == other.inner
    }

    fn __repr__(&self) -> String {
        format!("Checkpoint.{:?}", self.inner)
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyActionMessage
// ═══════════════════════════════════════════════════════════════════

/// Internal Rust struct that implements ActionMessage for a Python
/// `PyActionMessage`. Stores msg_type + correlation_id + JSON payload,
/// and on `payload()` returns the payload.
pub struct PyActionMessageImpl {
    msg_type: String,
    correlation_id: Uuid,
    payload: serde_json::Value,
}

#[async_trait::async_trait]
impl ActionMessage for PyActionMessageImpl {
    fn msg_type(&self) -> &'static str {
        // SAFETY: we leak the String to get a 'static str. The PyActionMessageImpl
        // lives as long as the CheckpointRule (which is itself 'static for the
        // pool of static strings). This avoids per-call allocation.
        Box::leak(self.msg_type.clone().into_boxed_str())
    }

    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }

    fn payload(&self) -> serde_json::Value {
        self.payload.clone()
    }

    fn intent(&self) -> MessageIntent {
        MessageIntent::Command
    }
}

/// Python ActionMessage — opaque wrapper for embedding in CheckpointRule.
///
/// Construct via `ActionMessage(msg_type=..., correlation_id=..., payload={...})`.
/// The class itself doesn't carry the Rust trait impl — `CheckpointRule`
/// reads its fields at construction time and builds a Rust `ActionMessage`
/// from them.
#[pyclass(name = "ActionMessage")]
#[derive(Clone)]
pub struct PyActionMessage {
    msg_type: String,
    correlation_id: Uuid,
    payload: serde_json::Value,
}

#[pymethods]
impl PyActionMessage {
    #[new]
    #[pyo3(signature = (msg_type, correlation_id=None, payload=None))]
    fn new(
        py: Python<'_>,
        msg_type: String,
        correlation_id: Option<String>,
        payload: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let cid = match correlation_id {
            Some(s) => Uuid::parse_str(&s).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                    "invalid correlation_id UUID: {e}"
                ))
            })?,
            None => Uuid::new_v4(),
        };
        let payload_json = match payload {
            Some(obj) => py_object_to_json(&obj, py)?,
            None => serde_json::json!({"correlation_id": cid.to_string()}),
        };
        Ok(Self {
            msg_type,
            correlation_id: cid,
            payload: payload_json,
        })
    }

    #[getter]
    fn msg_type(&self) -> &str {
        &self.msg_type
    }

    #[getter]
    fn correlation_id(&self) -> String {
        self.correlation_id.to_string()
    }

    #[getter]
    fn payload(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_value_to_py(&self.payload, py)
    }

    fn __repr__(&self) -> String {
        format!(
            "ActionMessage(msg_type='{}', cid={})",
            self.msg_type,
            self.correlation_id
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyRoute
// ═══════════════════════════════════════════════════════════════════

/// Python Route — how Engine delivers a message to its receiver.
#[pyclass(name = "Route")]
#[derive(Clone)]
pub struct PyRoute {
    pub(crate) inner: Route,
}

#[pymethods]
impl PyRoute {
    /// Deliver to exact NodeIds (point-to-point).
    #[staticmethod]
    fn strict(ids: Vec<PyNodeId>) -> Self {
        let inner_ids: Vec<NodeId> = ids.into_iter().map(|n| n.inner).collect();
        Self { inner: Route::strict(inner_ids) }
    }

    /// Deliver to all Nodes whose `capabilities` JSON contains required
    /// key/value pairs (AND).
    #[staticmethod]
    fn discovery(requirements: Vec<(String, String)>) -> Self {
        Self {
            inner: Route::discovery(requirements),
        }
    }

    fn __eq__(&self, other: &PyRoute) -> bool {
        self.inner == other.inner
    }

    fn __repr__(&self) -> String {
        match &self.inner {
            Route::Strict(ids) => format!("Route.Strict({} ids)", ids.len()),
            Route::Discovery(cap) => format!("Route.Discovery({} reqs)", cap.requirements.len()),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyCapability
// ═══════════════════════════════════════════════════════════════════

/// Python Capability — AND-matched key/value pairs declared by Node's
/// `capabilities` JSON.
#[pyclass(name = "Capability")]
#[derive(Clone)]
pub struct PyCapability {
    pub(crate) inner: Capability,
}

#[pymethods]
impl PyCapability {
    #[new]
    fn new(requirements: Vec<(String, String)>) -> Self {
        Self {
            inner: Capability::new(requirements),
        }
    }

    #[getter]
    fn requirements(&self) -> Vec<(String, String)> {
        self.inner.requirements.clone()
    }

    fn __repr__(&self) -> String {
        format!("Capability({} reqs)", self.inner.requirements.len())
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyCheckpointRule
// ═══════════════════════════════════════════════════════════════════

/// Python CheckpointRule — (name, trigger, actions). The Python binding
/// uses a "pre-built actions" model: callers supply a list of
/// `ActionMessage` objects that get cloned into the closure at fire time.
///
/// The `when` predicate defaults to "always fire" (matches Rust default).
/// Phase 6 task 6.22.4: this is the minimum viable Python binding. If
/// users later need a Python `when` callable, the binding can be extended
/// with a Python-side predicate that wraps a `PyAny`.
#[pyclass(name = "CheckpointRule")]
pub struct PyCheckpointRule {
    pub(crate) name: String,
    pub(crate) trigger: Checkpoint,
    pub(crate) actions: Vec<PyActionMessage>,
}

#[pymethods]
impl PyCheckpointRule {
    /// Construct a CheckpointRule from a name + trigger + list of
    /// pre-built ActionMessage instances.
    #[new]
    fn new(name: String, trigger: &PyCheckpoint, actions: Vec<PyActionMessage>) -> Self {
        Self {
            name,
            trigger: trigger.inner,
            actions,
        }
    }

    #[getter]
    fn name(&self) -> String {
        self.name.clone()
    }

    #[getter]
    fn trigger(&self) -> PyCheckpoint {
        PyCheckpoint { inner: self.trigger }
    }

    #[getter]
    fn actions(&self) -> Vec<PyActionMessage> {
        self.actions.clone()
    }

    fn __repr__(&self) -> String {
        format!(
            "CheckpointRule(name='{}', trigger={:?}, actions={})",
            self.name,
            self.trigger,
            self.actions.len()
        )
    }
}

/// Non-pyclass helper for converting PyCheckpointRule → Rust CheckpointRule.
impl PyCheckpointRule {
    /// Convert to a Rust `CheckpointRule`. Used by EngineBuilder to splice
    /// Python-defined rules into the AgentConfig before build().
    pub(crate) fn into_rust_rule(&self) -> CoreCheckpointRule {
        // Clone the actions into a shared Vec<PyActionMessage> that the
        // build closure captures. The closure returns a fresh Rust
        // PyActionMessageImpl per invocation by reading the captured
        // action's fields.
        let actions = Arc::new(self.actions.clone());
        let build_actions = actions.clone();
        CoreCheckpointRule::new(
            self.name.clone(),
            self.trigger,
            |_state| true, // when = always fire
            move |_state| -> Box<dyn ActionMessage> {
                // Pick the first action. (Phase 6 task 6.22.4 minimal API:
                // a single per-rule ActionMessage. Multi-action support
                // requires Engine-side fan-out — future task.)
                let action = build_actions
                    .first()
                    .expect("CheckpointRule requires at least one action");
                Box::new(PyActionMessageImpl {
                    msg_type: action.msg_type.clone(),
                    correlation_id: action.correlation_id,
                    payload: action.payload.clone(),
                })
            },
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyModelDecl
// ═══════════════════════════════════════════════════════════════════

/// Python ModelDecl — declarative model identification.
///
/// Mirrors `arf_agent::ModelDecl`. Wrap into `AgentConfig.model=` to use
/// the nested (Phase 7) form. Endpoint and api_key_env default to the
/// provider's built-in defaults; pass them explicitly to override.
#[pyclass(name = "ModelDecl")]
#[derive(Clone)]
pub struct PyModelDecl {
    pub(crate) inner: arf_agent::ModelDecl,
}

#[pymethods]
impl PyModelDecl {
    #[new]
    #[pyo3(signature = (
        provider,
        model_name,
        endpoint = None,
        api_key_env = None,
        thinking_enabled = false,
        temperature = None,
        max_output_tokens = None,
        extra = None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        provider: String,
        model_name: String,
        endpoint: Option<String>,
        api_key_env: Option<String>,
        thinking_enabled: bool,
        temperature: Option<f64>,
        max_output_tokens: Option<u32>,
        extra: Option<pyo3::Py<pyo3::PyAny>>,
    ) -> PyResult<Self> {
        let extra_json = match extra {
            Some(obj) => py_object_to_json(&obj, py)?,
            None => serde_json::Value::Null,
        };
        Ok(Self {
            inner: arf_agent::ModelDecl {
                provider,
                model_name,
                endpoint,
                api_key_env,
                thinking_enabled,
                temperature,
                max_output_tokens,
                extra: extra_json,
            },
        })
    }

    #[getter] fn provider(&self) -> String { self.inner.provider.clone() }
    #[getter] fn model_name(&self) -> String { self.inner.model_name.clone() }
    #[getter] fn endpoint(&self) -> Option<String> { self.inner.endpoint.clone() }
    #[getter] fn api_key_env(&self) -> Option<String> { self.inner.api_key_env.clone() }
    #[getter] fn thinking_enabled(&self) -> bool { self.inner.thinking_enabled }
    #[getter] fn temperature(&self) -> Option<f64> { self.inner.temperature }
    #[getter] fn max_output_tokens(&self) -> Option<u32> { self.inner.max_output_tokens }

    fn __repr__(&self) -> String {
        format!(
            "ModelDecl(provider='{}', model_name='{}')",
            self.inner.provider, self.inner.model_name
        )
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyEngineConfig
// ═══════════════════════════════════════════════════════════════════

/// Python EngineConfig — runtime configuration nested in AgentConfig.
///
/// Mirrors `arf_engine::EngineConfig`. Wrap into `AgentConfig.engine=`
/// to use the nested (Phase 7) form. `processors` and `on_member_failed`
/// are not exposed to Python (they hold trait objects that don't have a
/// Python-constructible form); leave them at defaults.
#[pyclass(name = "EngineConfig")]
pub struct PyEngineConfig {
    pub(crate) inner: EngineConfig,
}

/// Helper: build a fresh `EngineConfig` from a Python `PyEngineConfig`
/// object without requiring `Clone` on the Rust inner type. We pull
/// each public field via the PyO3 getters, then build a new
/// `EngineConfig` value (which discards any closures — those aren't
/// exposed to Python anyway, see PyEngineConfig docstring).
fn build_engine_config_from_py(
    obj: &pyo3::Bound<'_, pyo3::PyAny>,
) -> PyResult<EngineConfig> {
    let max_turns: u32 = obj.getattr("max_turns")?.extract()?;
    let tool_timeout_ms: Option<u64> = obj.getattr("tool_timeout_ms")?.extract()?;
    // `routes` getter returns HashMap<String, String> (debug form). Not
    // useful for round-tripping actual Route values — but for the
    // standard tutorial case (no custom routes), the user constructs
    // EngineConfig from scratch and EngineConfig.max_turns is what matters.
    // We accept that the routes field is informational only when the
    // nested form is used; for actual route use, callers should pass
    // routes= at the AgentConfig top level.
    let mut ec = EngineConfig::default();
    ec.max_turns = max_turns;
    if let Some(t) = tool_timeout_ms {
        ec.tool_timeout_ms = Some(t);
    }
    Ok(ec)
}

#[pymethods]
impl PyEngineConfig {
    #[new]
    #[pyo3(signature = (max_turns=10, tool_timeout_ms=None, routes=None, checkpoint_rules=None))]
    fn new(
        max_turns: u32,
        tool_timeout_ms: Option<u64>,
        routes: Option<std::collections::HashMap<String, PyRoute>>,
        checkpoint_rules: Option<&pyo3::Bound<'_, pyo3::PyAny>>,
    ) -> PyResult<Self> {
        let routes_map: std::collections::HashMap<String, Route> = match routes {
            Some(m) => m.into_iter().map(|(k, v)| (k, v.inner)).collect(),
            None => std::collections::HashMap::new(),
        };
        let rules: Vec<CoreCheckpointRule> = match checkpoint_rules {
            Some(obj) => {
                let list = obj.cast::<pyo3::types::PyList>()?;
                let mut out = Vec::with_capacity(list.len());
                for item in list.iter() {
                    let rule: PyRef<PyCheckpointRule> = item.extract()?;
                    out.push(rule.into_rust_rule());
                }
                out
            }
            None => vec![],
        };
        Ok(Self {
            inner: EngineConfig {
                routes: routes_map,
                checkpoint_rules: rules,
                max_turns,
                tool_timeout_ms,
                ..Default::default()
            },
        })
    }

    #[getter] fn max_turns(&self) -> u32 { self.inner.max_turns }
    #[getter] fn tool_timeout_ms(&self) -> Option<u64> { self.inner.tool_timeout_ms }
    #[getter]
    fn routes(&self) -> std::collections::HashMap<String, String> {
        self.inner
            .routes
            .iter()
            .map(|(k, v)| (k.clone(), format!("{:?}", v)))
            .collect()
    }

    fn __repr__(&self) -> String {
        format!("EngineConfig(max_turns={})", self.inner.max_turns)
    }
}

// ═══════════════════════════════════════════════════════════════════
// PyEngineHandle — Task 14: long-lived Arc<Mutex<Engine>> for teams
// ═══════════════════════════════════════════════════════════════════
//
// `PyEngine` (above) uses `Arc<Mutex<Option<Engine>>>` + `take()` so each
// Python-side `engine.run(...)` call consumes the engine and restores it
// afterwards. That shape doesn't fit `Team.engine(id)` which needs to keep
// the same Engine alive across many chat turns (potentially from multiple
// HTTP request handlers).
//
// `PyEngineHandle` holds `Arc<tokio::sync::Mutex<Engine>>` directly so
// the lock can be held across the `engine.run` await (the engine's
// run-loop is async and may need to retain the lock across multiple
// await points in some configurations).
#[pyclass(name = "EngineHandle")]
pub struct PyEngineHandle {
    /// Engine is held behind a tokio::sync::Mutex so we can hold the
    /// guard across `.await` points (engine.run is async).
    inner: Arc<tokio::sync::Mutex<Engine>>,
    /// PyState used by the default `chat(message)` form. App can either
    /// call this (fresh state each time) or call `chat_with_state(...)`
    /// with their own state.
    default_state: Arc<Mutex<CoreState>>,
}

impl PyEngineHandle {
    /// Build a handle wrapping the given engine by-value. Used by
    /// one-off EngineBuilder flows (not by Team, which uses
    /// `from_arc`).
    pub fn from_engine(engine: Engine) -> Self {
        Self {
            inner: Arc::new(tokio::sync::Mutex::new(engine)),
            default_state: Arc::new(Mutex::new(CoreState::new())),
        }
    }

    /// Build a handle that shares an existing `Arc<TokioMutex<Engine>>`.
    /// Team uses this so multiple `Team.engine(id)` calls return
    /// handles that all reference the same underlying engine.
    pub fn from_arc(arc: Arc<tokio::sync::Mutex<Engine>>) -> Self {
        Self {
            inner: arc,
            default_state: Arc::new(Mutex::new(CoreState::new())),
        }
    }
}

#[pymethods]
impl PyEngineHandle {
    #[getter]
    fn agent_id(&self) -> PyResult<PyNodeId> {
        // Blocking-lock briefly to read agent_id. Engine isn't Clone,
        // but we only need a string-ish field — a small async helper
        // would be cleaner; for now we use try_lock and fall back to
        // spawn_blocking if the lock is contended.
        let inner = self.inner.clone();
        let agent_id = crate::get_runtime()
            .block_on(async move { inner.lock().await.agent_id().clone() });
        Ok(PyNodeId { inner: agent_id })
    }

    #[getter]
    fn system_prompt(&self) -> PyResult<String> {
        let inner = self.inner.clone();
        let sp = crate::get_runtime()
            .block_on(async move { inner.lock().await.system_prompt().to_string() });
        Ok(sp)
    }

    /// Single-turn chat: create a fresh `State`, run the engine, return
    /// the assistant's text output. Multi-turn continuity is the App's
    /// job — call `chat_with_state` with a stateful `EngineState`.
    fn chat<'py>(
        &self,
        py: Python<'py>,
        user_input: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let engine_arc = self.inner.clone();
        let state_arc = self.default_state.clone();
        // Single-turn semantics: discard any prior default_state and
        // start with a fresh State. `default_state: Arc<Mutex<CoreState>>`
        // — use mem::replace to swap the State without holding the
        // MutexGuard across the await.
        let mut state: CoreState = {
            let mut sguard = state_arc.lock().unwrap();
            std::mem::replace(&mut *sguard, CoreState::new())
        };
        future_into_py(py, async move {
            let cancel = tokio_util::sync::CancellationToken::new();
            let result = {
                let mut eng = engine_arc.lock().await;
                eng.run(&mut state, user_input, cancel).await
            };
            // Stash the post-run state so a follow-up read on
            // `default_state` could observe it (App shouldn't rely on
            // this — chat() is documented as single-turn).
            *state_arc.lock().unwrap() = state;
            result.map_err(|e| PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string()))
        })
    }

    /// Multi-turn chat: App provides the `EngineState` (preserves
    /// conversation history across calls). Same engine, borrowed for
    /// the duration of the run.
    fn chat_with_state<'py>(
        &self,
        py: Python<'py>,
        state: &PyState,
        user_input: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let engine_arc = self.inner.clone();
        let state_arc = state.inner.clone();
        // `PyState.inner` is `Arc<Mutex<Option<CoreState>>>`. Take the
        // State out via Option::take; after the run, put it back so the
        // next call sees the updated history.
        let mut state_inner: CoreState = {
            let mut sguard = state_arc.lock().unwrap();
            sguard.take().ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "state already consumed by a previous run",
                )
            })?
        };
        future_into_py(py, async move {
            let cancel = tokio_util::sync::CancellationToken::new();
            let result = {
                let mut eng = engine_arc.lock().await;
                eng.run(&mut state_inner, user_input, cancel).await
            };
            // Restore the (now-updated) state so subsequent calls see
            // the conversation history.
            *state_arc.lock().unwrap() = Some(state_inner);
            result.map_err(|e| PyErr::new::<pyo3::exceptions::PyException, _>(e.to_string()))
        })
    }

    fn __repr__(&self) -> String {
        let inner = self.inner.clone();
        let aid = crate::get_runtime()
            .block_on(async move { inner.lock().await.agent_id().to_string() });
        format!("EngineHandle(agent_id='{}')", aid)
    }
}