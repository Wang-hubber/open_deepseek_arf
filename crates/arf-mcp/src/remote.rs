use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::config::RemoteConfig;
use crate::discovery::{DiscoveryBackend, ToolInfo};
use crate::error::McpError;
use crate::tool::Tool;
use crate::types::ToolError;

// ── MCP Protocol Types ─────────────────────────────────────────────

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
}

// ── HttpProxyTool ──────────────────────────────────────────────────

/// Wraps a remote MCP tool as a local `Tool` trait implementation.
/// `execute()` sends an HTTP `tools/call` request and extracts text content.
struct HttpProxyTool {
    name: String,
    description: String,
    schema: Value,
    config: RemoteConfig,
    http_client: reqwest::Client,
}

#[async_trait]
impl Tool for HttpProxyTool {
    fn name(&self) -> &str {
        &self.name
    }

    fn description(&self) -> &str {
        &self.description
    }

    fn parameters_schema(&self) -> Value {
        self.schema.clone()
    }

    async fn execute(&self, params: Value) -> Result<Value, ToolError> {
        let body = serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": self.name, "arguments": params},
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

        let resp = req.send().await.map_err(|e| ToolError::from(format!("HTTP error: {e}")))?;
        let text = resp.text().await.map_err(|e| ToolError::from(format!("read error: {e}")))?;
        let jrpc = parse_sse_or_json(&text);

        if let Some(err) = jrpc.error {
            return Err(ToolError::from(format!("MCP error [{}]: {}", err.code, err.message)));
        }

        let result = jrpc.result.unwrap_or_default();
        let content: CallToolResult = serde_json::from_value(result)
            .map_err(|e| ToolError::from(format!("parse error: {e}")))?;
        let text_out: String = content
            .content
            .into_iter()
            .filter_map(|c| if c.content_type == "text" { c.text } else { None })
            .collect::<Vec<_>>()
            .join("\n");

        Ok(Value::String(text_out))
    }
}

// ── HttpDiscovery ──────────────────────────────────────────────────

/// HTTP-based discovery: `initialize` + `tools/list` → tool registry.
pub struct HttpDiscovery {
    tool_info: Vec<ToolInfo>,
    tools: HashMap<String, Arc<dyn Tool>>,
}

impl HttpDiscovery {
    pub async fn connect(config: RemoteConfig) -> Result<Self, McpError> {
        let http = build_http_client(&config);

        // 1. initialize
        let init_resp = rpc_call(&http, &config, "initialize", serde_json::json!({
            "protocolVersion": "1.0",
            "clientInfo": {"name": "arf-mcp", "version": "1.0"},
            "capabilities": {},
        }))
        .await
        .map_err(|e| McpError::RemoteUnreachable { url: config.url.clone(), reason: format!("{e}") })?;

        if let Some(err) = init_resp.error {
            return Err(McpError::RemoteRejected { url: config.url.clone(), code: err.code, message: err.message });
        }

        // 2. tools/list
        let tools_resp = rpc_call(&http, &config, "tools/list", serde_json::json!({}))
            .await
            .map_err(|e| McpError::RemoteUnreachable { url: config.url.clone(), reason: format!("{e}") })?;

        let remote_tools: Vec<RemoteToolDef> = tools_resp
            .result
            .and_then(|r| r.get("tools").cloned())
            .and_then(|v| serde_json::from_value(v).ok())
            .unwrap_or_default();

        let mut tool_info = Vec::new();
        let mut tools: HashMap<String, Arc<dyn Tool>> = HashMap::new();

        for rt in remote_tools {
            tool_info.push(ToolInfo {
                name: rt.name.clone(),
                description: rt.description.clone(),
                parameters_schema: rt.input_schema.clone(),
            });
            let proxy = Arc::new(HttpProxyTool {
                name: rt.name.clone(),
                description: rt.description,
                schema: rt.input_schema,
                config: config.clone(),
                http_client: http.clone(),
            }) as Arc<dyn Tool>;
            tools.insert(rt.name, proxy);
        }

        Ok(Self { tool_info, tools })
    }
}

#[async_trait]
impl DiscoveryBackend for HttpDiscovery {
    fn list_tools(&self) -> &[ToolInfo] {
        &self.tool_info
    }
    fn tool_map(&self) -> &HashMap<String, Arc<dyn Tool>> {
        &self.tools
    }
    fn resolve_tool(&self, name: &str) -> Option<Arc<dyn Tool>> {
        self.tools.get(name).cloned()
    }
    // Skill methods use default (empty) implementations from DiscoveryBackend
}

// ── Helpers ────────────────────────────────────────────────────────

fn build_http_client(config: &RemoteConfig) -> reqwest::Client {
    let mut builder = reqwest::Client::builder();
    if let Some(ref ca_path) = config.tls_ca_cert {
        if let Ok(ca_bytes) = std::fs::read(ca_path) {
            if let Ok(cert) = reqwest::tls::Certificate::from_pem(&ca_bytes) {
                builder = builder.add_root_certificate(cert);
            }
        }
    }
    let mut headers = reqwest::header::HeaderMap::new();
    for (k, v) in &config.headers {
        if let (Ok(name), Ok(value)) = (
            reqwest::header::HeaderName::from_bytes(k.as_bytes()),
            reqwest::header::HeaderValue::from_str(v),
        ) {
            headers.insert(name, value);
        }
    }
    builder.default_headers(headers).build().unwrap_or_default()
}

async fn rpc_call(http: &reqwest::Client, config: &RemoteConfig, method: &str, params: Value) -> Result<JsonRpcResponse, reqwest::Error> {
    let body = serde_json::json!({"jsonrpc":"2.0","id":1,"method":method,"params":params});
    let mut req = http.post(&config.url).header("Content-Type", "application/json")
        .header("Accept", "application/json, text/event-stream").json(&body);
    if let Some(s) = config.timeout_secs { req = req.timeout(Duration::from_secs(s)); }
    let resp = req.send().await?;
    let text = resp.text().await?;
    Ok(parse_sse_or_json(&text))
}

fn parse_sse_or_json(text: &str) -> JsonRpcResponse {
    if let Ok(r) = serde_json::from_str::<JsonRpcResponse>(text) { return r; }
    for line in text.lines() {
        if let Some(data) = line.strip_prefix("data: ") {
            if let Ok(r) = serde_json::from_str::<JsonRpcResponse>(data) { return r; }
        }
    }
    JsonRpcResponse { result: None, error: Some(JsonRpcError { code: -32000, message: format!("parse failed: {text}") }) }
}
