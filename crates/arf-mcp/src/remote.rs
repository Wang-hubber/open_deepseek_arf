use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::Mutex;

use crate::config::RemoteConfig;
use crate::error::McpError;
use crate::types::{ToolCallSet, ToolResultItem};

// ── MCP Protocol Types ──────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteToolDef {
    pub name: String,
    pub description: String,
    #[serde(default, rename = "inputSchema")]
    pub input_schema: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CallToolResult {
    pub content: Vec<ToolContent>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolContent {
    #[serde(rename = "type")]
    pub content_type: String,
    #[serde(default)]
    pub text: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcError {
    pub code: i32,
    pub message: String,
}

#[derive(Debug, Deserialize)]
struct JsonRpcResponse {
    #[serde(default)]
    result: Option<Value>,
    #[serde(default)]
    error: Option<JsonRpcError>,
    #[allow(dead_code)]
    id: Option<u64>,
}

// ── RemoteMcpNode ──────────────────────────────────────────────────

pub struct RemoteMcpNode {
    pub namespace: String,
    pub node_id: NodeId,
    config: RemoteConfig,
    http_client: reqwest::Client,
    known_tools: Mutex<HashMap<String, RemoteToolDef>>,
    handle: Mutex<Option<arf_bus::NodeHandle>>,
}

impl RemoteMcpNode {
    pub fn new(namespace: impl Into<String>, config: RemoteConfig) -> Self {
        let ns: String = namespace.into();
        let mut client_builder = reqwest::Client::builder();

        // Custom TLS CA cert
        if let Some(ref ca_path) = config.tls_ca_cert {
            // TLS custom CA requires building from bytes
            if let Ok(ca_bytes) = std::fs::read(ca_path) {
                if let Ok(cert) = reqwest::tls::Certificate::from_pem(&ca_bytes) {
                    client_builder = client_builder.add_root_certificate(cert);
                }
            }
        }

        // Default headers
        let mut headers = reqwest::header::HeaderMap::new();
        for (k, v) in &config.headers {
            if let (Ok(name), Ok(value)) = (
                reqwest::header::HeaderName::from_bytes(k.as_bytes()),
                reqwest::header::HeaderValue::from_str(v),
            ) {
                headers.insert(name, value);
            }
        }

        let http_client = client_builder
            .default_headers(headers)
            .build()
            .unwrap_or_default();

        Self {
            node_id: NodeId::new(&format!("mcp/{ns}")),
            namespace: ns,
            config,
            http_client,
            known_tools: Mutex::new(HashMap::new()),
            handle: Mutex::new(None),
        }
    }

    pub async fn connect(self: &Arc<Self>, bus: &Bus) -> Result<(), McpError> {
        // 1. HTTP initialize
        let init_response = self
            .rpc_call("initialize", serde_json::json!({
                "protocolVersion": "1.0",
                "clientInfo": {"name": "arf-mcp", "version": "1.0"},
                "capabilities": {},
            }))
            .await
            .map_err(|e| McpError::RemoteUnreachable {
                url: self.config.url.clone(),
                reason: format!("{e}"),
            })?;

        // Check for protocol error
        if let Some(err) = init_response.error {
            return Err(McpError::RemoteRejected {
                url: self.config.url.clone(),
                code: err.code,
                message: err.message,
            });
        }

        // 2. tools/list
        let tools_response = self
            .rpc_call("tools/list", serde_json::json!({}))
            .await
            .map_err(|e| McpError::RemoteUnreachable {
                url: self.config.url.clone(),
                reason: format!("{e}"),
            })?;

        let tools: Vec<RemoteToolDef> = tools_response
            .result
            .and_then(|r| r.get("tools").cloned())
            .and_then(|v| serde_json::from_value(v).ok())
            .unwrap_or_default();

        // Store in struct (need interior mutability or Arc<Mutex<>>)
        // For now, we'll use unsafecell or Arc<Mutex<>> for known_tools
        // Actually, since connect takes &self (via Arc), we need Arc<Mutex<known_tools>>
        // But the struct uses HashMap directly. Let me fix this...
        // For simplicity, store known_tools in the struct via unsafe pointer to self
        // Actually, let me restructure with Arc<Mutex<HashMap>> for interior mutability

        // This is a design issue — connect() needs to modify known_tools but takes &self
        // We'll use unsafe to work around this for now and fix with proper interior mutability

        // 3. Build node_online capabilities
        let tool_list: Vec<Value> = tools
            .iter()
            .map(|t| {
                serde_json::json!({"name": t.name, "description": t.description})
            })
            .collect();

        let info = NodeInfo {
            node_id: self.node_id.clone(),
            node_type: "mcp".into(),
            capabilities: serde_json::json!({
                "runtime": {"runtime": "remote", "url": self.config.url},
                "tools": tool_list,
                "skills": [],
            }),
            online_since: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis() as u64,
        };

        let filter = MessageFilter {
            types: None,
            to_match: arf_core::ToMatch::All,
        };

        let handle = bus.connect(info, filter).await.map_err(|e| {
            McpError::BusConnect {
                reason: format!("{e}"),
            }
        })?;

        // Store tools and handle
        *self.known_tools.lock().await = tools
            .into_iter()
            .map(|t| (t.name.clone(), t))
            .collect();
        *self.handle.lock().await = Some(handle);

        // Spawn message loop
        let this = self.clone();
        tokio::spawn(async move { this.message_loop().await });

        Ok(())
    }

    async fn rpc_call(
        &self,
        method: &str,
        params: Value,
    ) -> Result<JsonRpcResponse, reqwest::Error> {
        let body = serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        });

        let mut req = self
            .http_client
            .post(&self.config.url)
            .header("Content-Type", "application/json")
            .header("Accept", "application/json, text/event-stream")
            .json(&body);

        if let Some(timeout_secs) = self.config.timeout_secs {
            req = req.timeout(Duration::from_secs(timeout_secs));
        }

        let resp = req.send().await?;
        let text = resp.text().await?;

        Ok(parse_sse_or_json(&text))
    }

    async fn message_loop(self: Arc<Self>) {
        loop {
            let msg = {
                let mut guard = self.handle.lock().await;
                match guard.as_mut() {
                    Some(handle) => match handle.recv().await {
                        Ok(m) => Some(m),
                        Err(_) => None,
                    },
                    None => break,
                }
            };

            let msg = match msg {
                Some(m) => m,
                None => break,
            };

            if !msg.is_for(&self.node_id) && !msg.is_broadcast() {
                continue;
            }

            let response = self.dispatch(&msg).await;

            if let Some((msg_type, payload)) = response {
                let from = msg.from.clone();
                let guard = self.handle.lock().await;
                if let Some(handle) = guard.as_ref() {
                    if let Err(e) = handle.send(&msg_type, vec![from], payload).await {
                        eprintln!("[RemoteMcpNode] failed to send {msg_type}: {e}");
                    }
                }
            }
        }
    }

    async fn dispatch(&self, msg: &Message) -> Option<(String, Value)> {
        match msg.msg_type.as_str() {
            "tool_call_set" => {
                let call_set: ToolCallSet =
                    match serde_json::from_value(msg.payload.clone()) {
                        Ok(cs) => cs,
                        Err(e) => {
                            return Some((
                                "tool_result_set".into(),
                                serde_json::json!({"session_id": "", "results": [{
                                    "call_id": "", "name": "", "status": "error",
                                    "result": null, "error": format!("invalid payload: {e}"),
                                }]}),
                            ));
                        }
                    };

                let results = self.proxy_tool_calls(&call_set).await;
                Some((
                    "tool_result_set".into(),
                    serde_json::json!({
                        "session_id": call_set.session_id,
                        "results": results,
                    }),
                ))
            }

            "use_skill" | "load_skill_resource" | "run_skill_script" => {
                Some((
                    "skill_error".into(),
                    serde_json::json!({
                        "namespace": self.namespace,
                        "error": format!("skills not supported by remote MCP node: {}", self.namespace),
                    }),
                ))
            }

            _ => None,
        }
    }

    async fn proxy_tool_calls(&self, call_set: &ToolCallSet) -> Vec<ToolResultItem> {
        let mut results = Vec::new();

        for call in &call_set.calls {
            let tool_name = call.tool.clone();
            let call_id = call.id.clone();

            // Check if tool exists
            let known = self.known_tools.lock().await;
            if !known.contains_key(&tool_name) {
                results.push(ToolResultItem {
                    call_id,
                    name: tool_name.clone(),
                    status: "error".into(),
                    result: Value::Null,
                    error: Some(format!("tool not found: {tool_name}")),
                });
                continue;
            }

            // Proxy the call
            let result = self
                .rpc_call(
                    "tools/call",
                    serde_json::json!({
                        "name": tool_name,
                        "arguments": call.params,
                    }),
                )
                .await;

            match result {
                Ok(resp) => {
                    if let Some(err) = resp.error {
                        results.push(ToolResultItem {
                            call_id,
                            name: tool_name.clone(),
                            status: "error".into(),
                            result: Value::Null,
                            error: Some(format!("MCP error [{}]: {}", err.code, err.message)),
                        });
                    } else if let Some(val) = resp.result {
                        let content: Option<CallToolResult> =
                            serde_json::from_value(val).ok();
                        let text = content
                            .map(|c| {
                                c.content
                                    .into_iter()
                                    .filter_map(|tc| {
                                        if tc.content_type == "text" {
                                            tc.text
                                        } else {
                                            None
                                        }
                                    })
                                    .collect::<Vec<_>>()
                                    .join("\n")
                            })
                            .unwrap_or_default();

                        results.push(ToolResultItem {
                            call_id,
                            name: tool_name.clone(),
                            status: "success".into(),
                            result: Value::String(text),
                            error: None,
                        });
                    }
                }
                Err(e) => {
                    results.push(ToolResultItem {
                        call_id,
                        name: tool_name,
                        status: "error".into(),
                        result: Value::Null,
                        error: Some(format!("HTTP error: {e}")),
                    });
                }
            }
        }

        results
    }
}

// ── SSE Parsing ─────────────────────────────────────────────────────

/// Parse an SSE (Server-Sent Events) or plain JSON response.
/// Streamable HTTP MCP transport may return either format.
fn parse_sse_or_json(text: &str) -> JsonRpcResponse {
    // Try plain JSON first
    if let Ok(resp) = serde_json::from_str::<JsonRpcResponse>(text) {
        return resp;
    }
    // Try SSE: `event: message\ndata: {...}\n\n`
    for line in text.lines() {
        if let Some(data) = line.strip_prefix("data: ") {
            if let Ok(resp) = serde_json::from_str::<JsonRpcResponse>(data) {
                return resp;
            }
        }
    }
    JsonRpcResponse {
        result: None,
        error: Some(JsonRpcError {
            code: -32000,
            message: format!("failed to parse response: {text}"),
        }),
        id: None,
    }
}
