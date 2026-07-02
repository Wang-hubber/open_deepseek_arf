//! E2EHarness — unified setup: tempdir + bus + nodes + engine.
//!
//! Builder pattern supports the most common variation points:
//! - `provider` — scripted mock (default for offline tests) or live MiniMax
//! - `with_mcp` — wire a filesystem-discovered McpNode rooted at the tempdir
//! - `max_turns` — override the engine's `max_turns` (default 10)
//! - `cancel` — inject a caller-controlled cancellation token
//! - `routes` — extra routes to register on the engine's AgentConfig
//!
//! Use [`E2EHarness::builder()`] for the full API or
//! [`E2EHarness::new()`] for the simple scripted-mock case.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo, Route, State, ToMatch};
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, ModelDecl, ResourceSpec, RunError};
use arf_model_adapter::{ModelAdapterNode, Provider};
use arf_mcp::McpNode;
use serde_json::json;
use tempfile::TempDir;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

/// Kind of provider the harness wires into the ModelAdapterNode.
pub enum ProviderKind {
    Mock(Arc<dyn Provider>),
    Live(Arc<dyn Provider>),
}

impl ProviderKind {
    fn as_dyn(&self) -> Arc<dyn Provider> {
        match self {
            ProviderKind::Mock(p) | ProviderKind::Live(p) => p.clone(),
        }
    }
}

/// Builder for [`E2EHarness`]. See module docs for the supported options.
pub struct E2EHarnessBuilder {
    provider: ProviderKind,
    with_mcp: bool,
    max_turns: u32,
    cancel: Option<CancellationToken>,
    extra_routes: HashMap<String, Route>,
    tool_timeout_ms: Option<u64>,
    /// Optional pre-created TempDir — used so the caller can write tool
    /// files into the same directory the harness will scan for MCP.
    tmpdir: Option<TempDir>,
    /// When true (default true), the harness injects a synthetic
    /// `tool_exec → tool_result` responder on the bus. The Engine sends
    /// `tool_exec`; the current McpNode listens to `tool_call_set`. This
    /// responder bridges the gap so the E2E tests don't have to wire
    /// the full pool-node → sub-bus → mcp-node chain.
    inject_tool_exec_responder: bool,
}

impl E2EHarnessBuilder {
    fn new(provider: ProviderKind) -> Self {
        Self {
            provider,
            with_mcp: false,
            max_turns: 10,
            cancel: None,
            extra_routes: HashMap::new(),
            tool_timeout_ms: None,
            tmpdir: None,
            inject_tool_exec_responder: true,
        }
    }

    /// Wire an McpNode rooted at the harness tempdir.
    pub fn with_mcp(mut self, v: bool) -> Self {
        self.with_mcp = v;
        self
    }

    /// Override the engine's `max_turns`.
    pub fn max_turns(mut self, n: u32) -> Self {
        self.max_turns = n;
        self
    }

    /// Inject a caller-controlled cancellation token (default: fresh token).
    pub fn cancel(mut self, c: CancellationToken) -> Self {
        self.cancel = Some(c);
        self
    }

    /// Register an additional route on the engine's AgentConfig.
    pub fn route(mut self, msg_type: &str, route: Route) -> Self {
        self.extra_routes.insert(msg_type.into(), route);
        self
    }

    /// Set the engine's `tool_timeout_ms` (default: None = no timeout).
    /// Useful for tests that want to fail fast on hung tool_exec calls.
    pub fn tool_timeout_ms(mut self, ms: u64) -> Self {
        self.tool_timeout_ms = Some(ms);
        self
    }

    /// Use a pre-created TempDir instead of letting the harness create one.
    /// The caller is responsible for keeping the TempDir alive (it is moved
    /// into the harness on build). Useful when tool files must be written
    /// to the dir BEFORE the harness scans it.
    pub fn tmpdir(mut self, t: TempDir) -> Self {
        self.tmpdir = Some(t);
        self
    }

    /// Disable the synthetic tool_exec responder (the default is to inject
    /// it). Use this when the test is going to wire a real tool receiver
    /// (e.g., a PoolNode + McpNode) and wants the engine to wait for the
    /// real responder.
    pub fn without_tool_exec_responder(mut self) -> Self {
        self.inject_tool_exec_responder = false;
        self
    }

    /// Build the harness.
    pub async fn build(self) -> anyhow::Result<E2EHarness> {
        E2EHarness::build_internal(
            self.provider,
            self.with_mcp,
            self.max_turns,
            self.cancel,
            self.extra_routes,
            self.tool_timeout_ms,
            self.tmpdir,
            self.inject_tool_exec_responder,
        )
        .await
    }
}

/// Unified E2E test setup. Holds the Bus, the (constructed-then-handed-off)
/// Engine, a fresh State, and the tempdir. The model and mcp nodes are held
/// alive by `_model_node` and `_mcp_node` — dropping the harness drops them
/// and disconnects.
pub struct E2EHarness {
    pub bus: Arc<Bus>,
    pub engine: Engine,
    pub state: State,
    pub tmpdir: TempDir,
    _model_node: Option<Arc<ModelAdapterNode>>,
    _mcp_node: Option<Arc<McpNode>>,
    pub cancel: Option<CancellationToken>,
}

impl E2EHarness {
    /// Create a harness with the simple scripted-mock defaults
    /// (no MCP, max_turns=10, fresh cancel). Use [`E2EHarness::builder`]
    /// for the full API.
    pub async fn new(provider: ProviderKind) -> anyhow::Result<Self> {
        Self::builder(provider).build().await
    }

    /// Start a builder. Use the builder's `.with_mcp`, `.max_turns`, etc.
    /// methods to customize the harness before calling `.build()`.
    pub fn builder(provider: ProviderKind) -> E2EHarnessBuilder {
        E2EHarnessBuilder::new(provider)
    }

    async fn build_internal(
        provider: ProviderKind,
        with_mcp: bool,
        max_turns: u32,
        cancel: Option<CancellationToken>,
        extra_routes: HashMap<String, Route>,
        tool_timeout_ms: Option<u64>,
        tmpdir: Option<TempDir>,
        inject_tool_exec_responder: bool,
    ) -> anyhow::Result<Self> {
        let bus = Arc::new(Bus::new(
            Duration::from_millis(500),
            Duration::from_secs(2),
            32,
        ));

        let tmpdir = match tmpdir {
            Some(t) => t,
            None => TempDir::new()?,
        };

        // Build AgentConfig. model_call auto-derived from ModelDecl.provider matching
        // node_type="model" capabilities; tool_exec auto-derived from ResourceSpec
        // pointing at the mcp node. extra_routes (custom msg_types) go in
        // engine.routes.
        let routes = extra_routes;

        let resources: Vec<ResourceSpec> = if with_mcp {
            // McpNode advertises capabilities.tools/skills at runtime; declare
            // a wildcard ResourceSpec so Registry picks up whatever tools the
            // filesystem-discovery finds in tmpdir.
            vec![ResourceSpec {
                resource_name: "mcp".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": "all"})),
            }]
        } else {
            vec![]
        };

        let cfg = AgentConfig {
            model: ModelDecl {
                provider: "scripted".into(),
                model_name: "scripted-v1".into(),
                ..Default::default()
            },
            resources,
            system_prompt_template: "You are helpful.".into(),
            initial_memory: vec![],
            allowed_paths: vec![],
            engine: EngineConfig {
                routes,
                checkpoint_rules: vec![],
                processors: HashMap::new(),
                on_member_failed: None,
                max_turns,
                tool_timeout_ms,
            },
        };

        // Real ModelAdapterNode — the engine-to-bus-to-node-to-provider chain.
        let model_node = ModelAdapterNode::new(
            provider.as_dyn(),
            &bus,
            NodeId::new("model/e2e"),
        )
        .await?;

        // Optional McpNode (filesystem-discovered at tmpdir).
        let mcp_node = if with_mcp {
            let mcp = McpNode::local("e2e", tmpdir.path().to_path_buf())?;
            mcp.connect(&bus).await.map_err(|e| anyhow::anyhow!("mcp connect: {e}"))?;
            Some(mcp)
        } else {
            None
        };

        // Optional synthetic `tool_exec → tool_result` responder.
        //
        // The Engine emits `tool_exec` messages. Today's `McpNode` listens
        // for `tool_call_set`. The responder bridges that gap so E2E tests
        // can drive the full ReAct tool loop without spinning up the
        // pool-node → sub-bus → mcp-node forwarding chain.
        //
        // Behaviour:
        // - Read `correlation_id` from the request payload
        // - Echo the tool name and a synthetic JSON response
        // - Reply on `tool_result` addressed to the requester, stamping
        //   the correlation_id so the engine matches it.
        //
        // Only registered when the caller asks for MCP AND wants the
        // default responder (i.e. the common case for the 5 react_loop tests).
        if inject_tool_exec_responder && with_mcp {
            let responder_id = NodeId::new("harness/tool_exec_responder");
            let responder_info = NodeInfo {
                node_id: responder_id.clone(),
                node_type: "harness-responder".into(),
                capabilities: json!({}),
                online_since: 0,
            };
            // ToMatch::All: we want every tool_exec on the bus, not just
            // ones routed to us. The engine's route sends tool_exec to the
            // mcp node, but our responder is a sibling test helper, so we
            // need to listen across the bus.
            let filter = MessageFilter {
                types: Some(vec!["tool_exec".into()]),
                to_match: ToMatch::All,
            };
            let mut handle = bus.connect(responder_info, filter).await?;
            let my_id = responder_id.clone();
            tokio::spawn(async move {
                while let Ok(msg) = handle.recv().await {
                    if msg.msg_type != "tool_exec" {
                        continue;
                    }
                    if !(msg.is_for(&my_id) || msg.is_broadcast()) {
                        continue;
                    }
                    let cid = msg
                        .payload
                        .get("correlation_id")
                        .and_then(|v| v.as_str())
                        .and_then(|s| Uuid::parse_str(s).ok());
                    let tool_name = msg
                        .payload
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown");
                    let result_payload = json!({
                        "name": tool_name,
                        "content": format!("synthetic-result-for-{tool_name}"),
                        "ok": true,
                    });
                    let _ = handle
                        .send_response(
                            "tool_result",
                            vec![msg.from.clone()],
                            result_payload,
                            cid,
                        )
                        .await;
                }
            });
        }

        let engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await?;

        Ok(Self {
            bus,
            engine,
            state: State::new(),
            tmpdir,
            _model_node: Some(model_node),
            _mcp_node: mcp_node,
            cancel,
        })
    }

    /// Run a single round with the given input. Uses the harness's
    /// [`CancellationToken`] if one was injected, otherwise creates a fresh
    /// one. Times out after 30 seconds.
    pub async fn run_react(&mut self, input: &str) -> Result<String, RunError> {
        let cancel = self
            .cancel
            .clone()
            .unwrap_or_else(CancellationToken::new);
        tokio::time::timeout(
            Duration::from_secs(30),
            self.engine.run(&mut self.state, input.into(), cancel),
        )
        .await
        .expect("run timed out")
    }

    /// Assert that the state contains exactly `n` messages.
    pub fn assert_state_messages(&self, n: usize) {
        assert_eq!(
            self.state.messages.len(),
            n,
            "expected {} messages, got {}",
            n,
            self.state.messages.len()
        );
    }

    /// Assert that the most recent assistant message with tool_calls has
    /// `tool_calls[0].name == name`. Panics if no such message exists.
    pub fn assert_last_tool_call(&self, name: &str) {
        let last = self
            .state
            .messages
            .iter()
            .rev()
            .find(|m| m.role == "assistant" && !m.tool_calls.is_empty())
            .expect("no assistant message with tool_calls");
        assert_eq!(
            last.tool_calls[0].name, name,
            "expected tool {}, got {}",
            name, last.tool_calls[0].name
        );
    }

    /// Return the tempdir path (for tests that need to write tool files
    /// after harness construction).
    pub fn tmpdir_path(&self) -> PathBuf {
        self.tmpdir.path().to_path_buf()
    }
}
