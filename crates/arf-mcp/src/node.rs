use std::path::PathBuf;
use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo};
use serde_json::Value;
use tokio::sync::Mutex;

use crate::config::RemoteConfig;
use crate::discovery::DiscoveryBackend;
use crate::error::McpError;
use crate::runtime::RuntimeModule;
use crate::types::{ToolCallItem, ToolCallSet};

/// The single MCP node type — local and remote differ only in backend.
pub struct McpNode {
    pub namespace: String,
    pub node_id: NodeId,
    discovery: Box<dyn DiscoveryBackend>,
    runtime: Box<dyn RuntimeModule>,
    handle: Mutex<Option<arf_bus::NodeHandle>>,
}

impl McpNode {
    // ── Constructors ──────────────────────────────────────────────

    /// Local MCP — filesystem scan + LocalRuntime.
    pub fn local(namespace: impl Into<String>, root: PathBuf) -> Result<Arc<Self>, McpError> {
        let ns: String = namespace.into();
        let discovery = crate::discovery::FsDiscovery::scan(root)?;
        Ok(Arc::new(Self {
            node_id: NodeId::new(&format!("mcp/{ns}")),
            namespace: ns,
            discovery: Box::new(discovery),
            runtime: Box::new(crate::runtime::LocalRuntime),
            handle: Mutex::new(None),
        }))
    }

    /// Remote MCP — HTTP discovery + RemoteRuntime.
    pub async fn remote(namespace: impl Into<String>, config: RemoteConfig) -> Result<Arc<Self>, McpError> {
        let ns: String = namespace.into();
        let discovery = crate::remote::HttpDiscovery::connect(config).await?;
        Ok(Arc::new(Self {
            node_id: NodeId::new(&format!("mcp/{ns}")),
            namespace: ns,
            discovery: Box::new(discovery),
            runtime: Box::new(crate::runtime::RemoteRuntime),
            handle: Mutex::new(None),
        }))
    }

    /// Local MCP + custom RuntimeModule.
    pub fn local_with_runtime(
        namespace: impl Into<String>,
        root: PathBuf,
        runtime: Box<dyn RuntimeModule>,
    ) -> Result<Arc<Self>, McpError> {
        let ns: String = namespace.into();
        let discovery = crate::discovery::FsDiscovery::scan(root)?;
        Ok(Arc::new(Self {
            node_id: NodeId::new(&format!("mcp/{ns}")),
            namespace: ns,
            discovery: Box::new(discovery),
            runtime,
            handle: Mutex::new(None),
        }))
    }

    /// Fully custom MCP node — inject any [`DiscoveryBackend`] + [`RuntimeModule`]
    /// (Phase 9 F-010). Previously the only entry points supplied a
    /// framework-chosen discovery (`FsDiscovery`/`HttpDiscovery`), so apps had to
    /// fork the crate to plug in their own backend.
    pub fn with_discovery(
        namespace: impl Into<String>,
        discovery: Box<dyn DiscoveryBackend>,
        runtime: Box<dyn RuntimeModule>,
    ) -> Arc<Self> {
        let ns: String = namespace.into();
        Arc::new(Self {
            node_id: NodeId::new(&format!("mcp/{ns}")),
            namespace: ns,
            discovery,
            runtime,
            handle: Mutex::new(None),
        })
    }

    /// Borrow the discovery backend (read-only access for inspection).
    pub fn discovery(&self) -> &dyn DiscoveryBackend {
        &*self.discovery
    }

    // ── Lifecycle ─────────────────────────────────────────────────

    pub async fn connect(self: &Arc<Self>, bus: &Bus) -> Result<(), McpError> {
        let info = self.build_node_info();
        let filter = MessageFilter { types: None, to_match: arf_core::ToMatch::All };
        let handle = bus.connect(info, filter).await.map_err(|e| McpError::BusConnect { reason: format!("{e}") })?;
        *self.handle.lock().await = Some(handle);

        let this = self.clone();
        tokio::spawn(async move { this.message_loop().await });
        Ok(())
    }

    // ── Internals ─────────────────────────────────────────────────

    fn build_node_info(&self) -> NodeInfo {
        let tools: Vec<Value> = self.discovery.list_tools().iter()
            .map(|t| serde_json::json!({
                "name": t.name,
                "description": t.description,
                "params_schema": t.parameters_schema,
            }))
            .collect();
        let skills: Vec<Value> = self.discovery.list_skills().iter()
            .map(|s| serde_json::json!({"name": s.name, "description": s.description}))
            .collect();

        NodeInfo {
            node_id: self.node_id.clone(),
            node_type: "mcp".into(),
            capabilities: serde_json::json!({"runtime": self.runtime.capabilities(), "tools": tools, "skills": skills}),
            online_since: std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis() as u64,
        }
    }

    async fn message_loop(self: Arc<Self>) {
        loop {
            let msg = {
                let mut guard = self.handle.lock().await;
                match guard.as_mut() {
                    Some(handle) => match handle.recv().await { Ok(m) => Some(m), Err(_) => None },
                    None => break,
                }
            };
            let msg = match msg { Some(m) => m, None => break };

            if !msg.is_for(&self.node_id) && !msg.is_broadcast() { continue; }

            let response = self.dispatch(&msg).await;
            if let Some((msg_type, payload)) = response {
                let from = msg.from.clone();
                let guard = self.handle.lock().await;
                if let Some(handle) = guard.as_ref() {
                    if let Err(e) = handle.send(&msg_type, vec![from], payload).await {
                        eprintln!("[McpNode] failed to send {msg_type}: {e}");
                    }
                }
            }
        }
    }

    async fn dispatch(&self, msg: &Message) -> Option<(String, Value)> {
        match msg.msg_type.as_str() {
            "tool_call_set" => {
                let call_set: ToolCallSet = match serde_json::from_value(msg.payload.clone()) {
                    Ok(cs) => cs,
                    Err(e) => return Some(("tool_result_set".into(), serde_json::json!({
                        "session_id": "", "results": [{"call_id":"","name":"","status":"error","result":null,"error":format!("invalid payload: {e}")}],
                    }))),
                };
                let result_set = self.runtime.execute(&call_set, self.discovery.tool_map()).await;
                Some(("tool_result_set".into(), serde_json::to_value(&result_set).unwrap_or_default()))
            }

            // Engine-native wire format (Phase 6 §3.4). Translate to
            // tool_call_set, execute, then unwrap the single result back
            // into the engine's `tool_result` schema.
            //
            // Design intent (2026-07-02): "whoever registers the Tool
            // responds to that tool's execution". When the engine
            // broadcasts `tool_exec` to multiple MCP nodes, only the
            // owner of `tool_name` responds. Non-owners return `None`
            // and stay silent — the engine waits for whoever replies.
            "tool_exec" => {
                let tool_name = msg
                    .payload
                    .get("tool_name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let arguments = msg
                    .payload
                    .get("arguments")
                    .cloned()
                    .unwrap_or(Value::Null);
                let correlation_id = msg
                    .payload
                    .get("correlation_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if tool_name.is_empty() {
                    return Some(("tool_result".into(), serde_json::json!({
                        "correlation_id": correlation_id,
                        "name": "",
                        "ok": false,
                        "error": "missing tool_name in tool_exec payload",
                    })));
                }
                // Filter: only the node that owns this tool responds.
                if self.discovery.resolve_tool(&tool_name).is_none() {
                    return None;
                }
                let call_set = ToolCallSet {
                    session_id: correlation_id.clone(),
                    calls: vec![ToolCallItem {
                        id: "call_0".into(),
                        tool: tool_name.clone(),
                        params: arguments,
                        blocked_by: vec![],
                        blocking: vec![],
                    }],
                    timeout_ms: None,
                };
                let result_set = self.runtime.execute(&call_set, self.discovery.tool_map()).await;
                let item = result_set.results.into_iter().next();
                let payload = match item {
                    Some(r) => serde_json::json!({
                        "correlation_id": correlation_id,
                        "name": r.name,
                        "ok": r.status == "success",
                        "content": r.result,
                        "error": r.error,
                    }),
                    None => serde_json::json!({
                        "correlation_id": correlation_id,
                        "name": tool_name,
                        "ok": false,
                        "error": "executor returned no result",
                    }),
                };
                Some(("tool_result".into(), payload))
            }

            "use_skill" => {
                let name = msg.payload.get("name").and_then(|v| v.as_str()).unwrap_or("");
                match (self.discovery.load_skill_body(name), self.discovery.load_skill_resources(name)) {
                    (Some(b), Some(r)) => {
                        let entry = self.discovery.resolve_skill(name);
                        Some(("skill_loaded".into(), serde_json::json!({
                            "namespace": self.namespace, "name": name,
                            "description": entry.map(|e| e.description.as_str()).unwrap_or(""),
                            "body": b, "resources": {"tools": r.tools, "references": r.references, "assets": r.assets},
                        })))
                    }
                    _ => Some(("skill_error".into(), serde_json::json!({"namespace":self.namespace,"name":name,"error":format!("skill not found: {name}")}))),
                }
            }

            "load_skill_resource" => {
                let sn = msg.payload.get("skill_name").and_then(|v| v.as_str()).unwrap_or("");
                let rp = msg.payload.get("resource_path").and_then(|v| v.as_str()).unwrap_or("");
                match self.discovery.load_resource_file(sn, rp) {
                    Ok(loaded) => Some(("skill_resource_loaded".into(), serde_json::json!({
                        "namespace":self.namespace, "skill_name":sn, "resource_path":rp,
                        "content": loaded.content, "description": loaded.description, "params_schema": loaded.params_schema,
                    }))),
                    Err(e) => Some(("skill_resource_error".into(), serde_json::json!({
                        "namespace":self.namespace, "skill_name":sn, "resource_path":rp, "error":e,
                    }))),
                }
            }

            "run_skill_script" => {
                let sn = msg.payload.get("skill_name").and_then(|v| v.as_str()).unwrap_or("");
                let tn = msg.payload.get("tool_name").and_then(|v| v.as_str()).unwrap_or("");
                let cid = msg.payload.get("call_id").and_then(|v| v.as_str()).unwrap_or("");
                let params = msg.payload.get("params").cloned().unwrap_or(Value::Null);

                let (status, result, error): (String, Value, Option<String>) =
                    match self.discovery.run_skill_tool(sn, tn, params).await {
                        Ok(val) => ("success".into(), val, None),
                        Err(e) => ("error".into(), Value::Null, Some(e)),
                    };

                Some(("skill_script_result".into(), serde_json::json!({
                    "session_id": msg.payload.get("session_id"), "call_id": cid,
                    "name": format!("{sn}/{tn}"), "status": status, "result": result, "error": error,
                })))
            }

            _ => None,
        }
    }
}
