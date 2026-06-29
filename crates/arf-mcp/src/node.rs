use std::path::PathBuf;
use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo};
use serde_json::Value;
use tokio::sync::Mutex;

use crate::discovery::DiscoveryModule;
use crate::error::McpError;
use crate::runtime::RuntimeModule;
use crate::types::ToolCallSet;

/// A local MCP node — discovers tools and skills from the filesystem.
///
/// ## Lifecycle
/// ```text
/// LocalMcpNode::new(ns, root)  → scan filesystem
///   .connect(&bus)             → Bus connect + node_online broadcast + spawn loop
/// ```
///
/// Use `Arc<LocalMcpNode>` when calling `connect()` — the message loop
/// holds an Arc reference to keep the node alive.
pub struct LocalMcpNode {
    pub namespace: String,
    pub node_id: NodeId,
    discovery: DiscoveryModule,
    runtime: Box<dyn RuntimeModule>,
    handle: Mutex<Option<arf_bus::NodeHandle>>,
}

impl LocalMcpNode {
    /// Scan the filesystem with the default local runtime.
    pub fn new(namespace: impl Into<String>, root_dir: PathBuf) -> Result<Self, McpError> {
        let ns: String = namespace.into();
        let discovery = DiscoveryModule::scan(root_dir)?;
        Ok(Self {
            node_id: NodeId::new(&format!("mcp/{ns}")),
            namespace: ns,
            discovery,
            runtime: Box::new(crate::runtime::LocalRuntime),
            handle: Mutex::new(None),
        })
    }

    /// Scan the filesystem with a custom RuntimeModule.
    pub fn with_runtime(
        namespace: impl Into<String>,
        root_dir: PathBuf,
        runtime: Box<dyn RuntimeModule>,
    ) -> Result<Self, McpError> {
        let ns: String = namespace.into();
        let discovery = DiscoveryModule::scan(root_dir)?;
        Ok(Self {
            node_id: NodeId::new(&format!("mcp/{ns}")),
            namespace: ns,
            discovery,
            runtime,
            handle: Mutex::new(None),
        })
    }

    /// Connect to the Bus, broadcast `node_online`, and start the message loop.
    ///
    /// Takes `Arc<Self>` so the spawned message loop can hold a reference.
    pub async fn connect(self: &Arc<Self>, bus: &Bus) -> Result<(), McpError> {
        let info = self.build_node_info();

        let filter = MessageFilter {
            types: None,
            to_match: arf_core::ToMatch::All,
        };

        let handle = bus.connect(info, filter).await.map_err(|e| {
            McpError::BusConnect {
                reason: format!("{e}"),
            }
        })?;

        *self.handle.lock().await = Some(handle);

        // Spawn the message loop with a clone of the Arc
        let this = self.clone();
        tokio::spawn(async move { this.message_loop().await });

        Ok(())
    }

    // ── Internals ──────────────────────────────────────────────────

    fn build_node_info(&self) -> NodeInfo {
        let tools: Vec<Value> = self
            .discovery
            .list_tools()
            .iter()
            .map(|t| {
                serde_json::json!({
                    "name": t.name,
                    "description": t.description,
                })
            })
            .collect();

        let skills: Vec<Value> = self
            .discovery
            .list_skills()
            .iter()
            .map(|s| {
                serde_json::json!({
                    "name": s.name,
                    "description": s.description,
                })
            })
            .collect();

        let capabilities = serde_json::json!({
            "runtime": self.runtime.capabilities(),
            "tools": tools,
            "skills": skills,
        });

        NodeInfo {
            node_id: self.node_id.clone(),
            node_type: "mcp".into(),
            capabilities,
            online_since: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis() as u64,
        }
    }

    async fn message_loop(self: Arc<Self>) {
        loop {
            // Receive next message — hold lock only during recv
            let msg = {
                let mut guard = self.handle.lock().await;
                match guard.as_mut() {
                    Some(handle) => match handle.recv().await {
                        Ok(m) => Some(m),
                        Err(_) => None, // Bus closed or lagged
                    },
                    None => break,
                }
            };

            let msg = match msg {
                Some(m) => m,
                None => break,
            };

            // Only respond to messages directed to us
            if !msg.is_for(&self.node_id) && !msg.is_broadcast() {
                continue;
            }

            let response = self.dispatch(&msg).await;

            // Send response back to the original sender
            if let Some((msg_type, payload)) = response {
                let from = msg.from.clone();
                let guard = self.handle.lock().await;
                if let Some(handle) = guard.as_ref() {
                    if let Err(e) = handle.send(&msg_type, vec![from], payload).await {
                        eprintln!("[LocalMcpNode] failed to send {msg_type}: {e}");
                    }
                }
            }
        }
    }

    async fn dispatch(&self, msg: &Message) -> Option<(String, Value)> {
        let payload = msg.payload.clone();

        match msg.msg_type.as_str() {
            "tool_call_set" => {
                let call_set: ToolCallSet = match serde_json::from_value(payload) {
                    Ok(cs) => cs,
                    Err(e) => {
                        return Some((
                            "tool_result_set".into(),
                            serde_json::json!({
                                "session_id": "",
                                "results": [{
                                    "call_id": "",
                                    "name": "",
                                    "status": "error",
                                    "result": null,
                                    "error": format!("invalid tool_call_set payload: {e}"),
                                }],
                            }),
                        ));
                    }
                };

                let result_set = self
                    .runtime
                    .execute(&call_set, self.discovery.tool_map())
                    .await;

                Some((
                    "tool_result_set".into(),
                    serde_json::to_value(&result_set).unwrap_or_default(),
                ))
            }

            "use_skill" => {
                let skill_name = payload.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let body = self.discovery.load_skill_body(skill_name);
                let resources = self.discovery.load_skill_resources(skill_name);

                match (body, resources) {
                    (Some(b), Some(r)) => {
                        let entry = self.discovery.resolve_skill(skill_name);
                        Some((
                            "skill_loaded".into(),
                            serde_json::json!({
                                "namespace": self.namespace,
                                "name": skill_name,
                                "description": entry.map(|e| e.description.as_str()).unwrap_or(""),
                                "body": b,
                                "resources": {
                                    "tools": r.tools,
                                    "references": r.references,
                                    "assets": r.assets,
                                },
                            }),
                        ))
                    }
                    _ => Some((
                        "skill_error".into(),
                        serde_json::json!({
                            "namespace": self.namespace,
                            "name": skill_name,
                            "error": format!("skill not found: {skill_name}"),
                        }),
                    )),
                }
            }

            "load_skill_resource" => {
                let skill_name =
                    payload.get("skill_name").and_then(|v| v.as_str()).unwrap_or("");
                let resource_path = payload
                    .get("resource_path")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                match self
                    .discovery
                    .load_resource_file(skill_name, resource_path)
                {
                    Ok(loaded) => Some((
                        "skill_resource_loaded".into(),
                        serde_json::json!({
                            "namespace": self.namespace,
                            "skill_name": skill_name,
                            "resource_path": resource_path,
                            "content": loaded.content,
                            "description": loaded.description,
                            "params_schema": loaded.params_schema,
                        }),
                    )),
                    Err(e) => Some((
                        "skill_resource_error".into(),
                        serde_json::json!({
                            "namespace": self.namespace,
                            "skill_name": skill_name,
                            "resource_path": resource_path,
                            "error": e,
                        }),
                    )),
                }
            }

            "run_skill_script" => {
                let skill_name =
                    payload.get("skill_name").and_then(|v| v.as_str()).unwrap_or("");
                let tool_name =
                    payload.get("tool_name").and_then(|v| v.as_str()).unwrap_or("");
                let call_id = payload.get("call_id").and_then(|v| v.as_str()).unwrap_or("");
                let params = payload.get("params").cloned().unwrap_or(Value::Null);

                let (status, result, error): (String, Value, Option<String>) = match self
                    .discovery
                    .run_skill_tool(skill_name, tool_name, params)
                    .await
                {
                    Ok(val) => ("success".into(), val, None),
                    Err(e) => ("error".into(), Value::Null, Some(e)),
                };

                Some((
                    "skill_script_result".into(),
                    serde_json::json!({
                        "session_id": payload.get("session_id"),
                        "call_id": call_id,
                        "name": format!("{skill_name}/{tool_name}"),
                        "status": status,
                        "result": result,
                        "error": error,
                    }),
                ))
            }

            _ => None, // unknown message type — ignore
        }
    }
}
