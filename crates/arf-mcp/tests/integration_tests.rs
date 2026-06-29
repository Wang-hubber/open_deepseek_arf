//! Integration tests — MiniEngine + Local fixtures + CodeTidy remote + ModelAdapter.
//!
//! These tests verify the MCP framework's integration across real Bus connections,
//! real tool execution, and ModelAdapter conversion. Remote tests require network
//! access to CodeTidy (https://mcp.codetidy.dev).
//!
//! Run:
//!   cargo test -p arf-mcp --test integration_tests
//! Run local-only:
//!   cargo test -p arf-mcp --test integration_tests -- local

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{MessageFilter, ModelMessage, NodeId, NodeInfo, ToMatch};
use arf_mcp::config::RemoteConfig;
use arf_mcp::types::ToolResultItem;
use arf_mcp::McpNode;
use arf_model_adapter::convert::tool_result_to_model_message;

// ═══════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════

fn test_bus() -> Bus {
    Bus::new(Duration::from_secs(10), Duration::from_secs(30), 128)
}

fn fixtures_root() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

fn codetidy_config() -> RemoteConfig {
    RemoteConfig {
        transport: "streamable-http".into(),
        url: "https://mcp.codetidy.dev".into(),
        timeout_secs: Some(30),
        headers: HashMap::new(),
        tls_ca_cert: None,
        retry: None,
    }
}

/// Tool metadata extracted from node_online for routing.
#[derive(Debug, Clone)]
struct McpToolInfo {
    name: String,
    description: String,
}

/// Registered MCP node state.
#[derive(Debug, Clone)]
struct McpNodeEntry {
    node_id: NodeId,
    #[allow(dead_code)]
    namespace: String,
    #[allow(dead_code)]
    tools: Vec<McpToolInfo>,
}

// ═══════════════════════════════════════════════════════════════════
// MiniEngine
// ═══════════════════════════════════════════════════════════════════

/// A minimal Engine for integration testing.
///
/// Connects to the Bus as an engine node, discovers MCP nodes via
/// `node_online`, routes `tool_call_set` by namespace, and converts
/// `tool_result_set` to `ModelMessage` via ModelAdapter.
struct MiniEngine {
    handle: arf_bus::NodeHandle,
    session_id: String,
    mcp_nodes: HashMap<String, McpNodeEntry>,
}

impl MiniEngine {
    async fn new(bus: &Bus, session_id: &str) -> Self {
        let handle = bus
            .connect(
                NodeInfo {
                    node_id: NodeId::new(format!("engine/{session_id}")),
                    node_type: "engine".into(),
                    capabilities: serde_json::json!({}),
                    online_since: 0,
                },
                MessageFilter {
                    types: Some(vec![
                        "tool_result_set".into(),
                        "skill_error".into(),
                        "skill_loaded".into(),
                        "skill_resource_loaded".into(),
                        "skill_script_result".into(),
                    ]),
                    to_match: ToMatch::All,
                },
            )
            .await
            .unwrap();

        MiniEngine { handle, session_id: session_id.into(), mcp_nodes: HashMap::new() }
    }

    fn register_mcp(&mut self, namespace: &str, node_id: NodeId, tools: Vec<McpToolInfo>) {
        self.mcp_nodes.insert(
            namespace.into(),
            McpNodeEntry { node_id, namespace: namespace.into(), tools },
        );
    }

    fn namespaces(&self) -> Vec<&str> {
        self.mcp_nodes.keys().map(|s| s.as_str()).collect()
    }

    #[allow(dead_code)]
    fn tool_names(&self, namespace: &str) -> Option<Vec<String>> {
        self.mcp_nodes.get(namespace).map(|e| {
            e.tools.iter().map(|t| t.name.clone()).collect()
        })
    }

    /// Send a tool_call_set to the MCP node for the given namespace,
    /// wait for tool_result_set, and convert each result to ModelMessage.
    async fn call_tool(
        &mut self,
        namespace: &str,
        tool_name: &str,
        params: serde_json::Value,
    ) -> Result<Vec<ModelMessage>, String> {
        let entry = self.mcp_nodes.get(namespace)
            .ok_or_else(|| format!("unknown namespace: {namespace}"))?;

        let call_id = "c0";
        self.handle
            .send(
                "tool_call_set",
                vec![entry.node_id.clone()],
                serde_json::json!({
                    "session_id": self.session_id,
                    "calls": [{"id": call_id, "tool": tool_name, "params": params}],
                }),
            )
            .await
            .map_err(|e| format!("send error: {e}"))?;

        let resp = self.handle.recv().await
            .map_err(|e| format!("recv error: {e}"))?;

        if resp.msg_type != "tool_result_set" {
            return Err(format!("unexpected msg_type: {}", resp.msg_type));
        }

        let results: Vec<ToolResultItem> = serde_json::from_value(
            resp.payload["results"].clone(),
        )
        .map_err(|e| format!("parse error: {e}"))?;

        let messages: Vec<ModelMessage> = results
            .iter()
            .map(|item| tool_result_to_model_message(item))
            .collect();

        Ok(messages)
    }

    /// Send tool_call_set with multiple calls, return raw ToolResultItems.
    #[allow(dead_code)]
    async fn call_tools_batch(
        &mut self,
        namespace: &str,
        calls: serde_json::Value,
    ) -> Result<Vec<ToolResultItem>, String> {
        let entry = self.mcp_nodes.get(namespace)
            .ok_or_else(|| format!("unknown namespace: {namespace}"))?;

        self.handle
            .send(
                "tool_call_set",
                vec![entry.node_id.clone()],
                serde_json::json!({
                    "session_id": self.session_id,
                    "calls": calls,
                }),
            )
            .await
            .map_err(|e| format!("send error: {e}"))?;

        let resp = self.handle.recv().await
            .map_err(|e| format!("recv error: {e}"))?;

        serde_json::from_value(resp.payload["results"].clone())
            .map_err(|e| format!("parse error: {e}"))
    }
}

/// Subscribe to Bus, drain messages until `node_online` for the given
/// namespace, return (node_id, tools).
async fn wait_for_node_online(
    rx: &mut tokio::sync::broadcast::Receiver<arf_core::Message>,
    namespace: &str,
) -> (NodeId, Vec<McpToolInfo>) {
    loop {
        let m = rx.recv().await.unwrap();
        let expected_prefix = format!("mcp/{namespace}");
        let payload_node_id = m.payload["node_id"].as_str().unwrap_or("");
        if m.msg_type == "node_online" && payload_node_id == expected_prefix {
            let tools: Vec<McpToolInfo> = m.payload["capabilities"]["tools"]
                .as_array()
                .unwrap()
                .iter()
                .map(|t| McpToolInfo {
                    name: t["name"].as_str().unwrap().into(),
                    description: t["description"].as_str().unwrap_or("").into(),
                })
                .collect();
            let node_id = NodeId::new(payload_node_id);
            return (node_id, tools);
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// Local + MiniEngine + fixtures — 3 tests
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn local_engine_read_file_to_model_message() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let test_file = fixtures_root().join("tools/read_file/main.py");
    let messages = engine
        .call_tool("fs", "read_file", serde_json::json!({
            "path": test_file.to_str().unwrap(),
        }))
        .await
        .unwrap();

    assert_eq!(messages.len(), 1);
    let msg = &messages[0];
    assert_eq!(msg.role, "tool");
    assert!(msg.content.contains("#!/usr/bin/env python3"));
    assert_eq!(msg.tool_call_id.as_deref(), Some("c0"));
    assert_eq!(msg.name.as_deref(), Some("read_file"));
}

#[tokio::test]
async fn local_engine_write_then_read() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let tmp_dir = std::env::temp_dir().join("arf_int_engine_wr");
    let _ = std::fs::remove_dir_all(&tmp_dir);
    std::fs::create_dir_all(&tmp_dir).unwrap();
    let file_path = tmp_dir.join("hello.txt");

    // Step 1: write
    let messages = engine
        .call_tool("fs", "write_file", serde_json::json!({
            "path": file_path.to_str().unwrap(),
            "content": "Hello from MiniEngine!",
        }))
        .await
        .unwrap();
    assert!(messages[0].content.contains("ok"));

    // Step 2: read back
    let messages = engine
        .call_tool("fs", "read_file", serde_json::json!({
            "path": file_path.to_str().unwrap(),
        }))
        .await
        .unwrap();
    assert!(messages[0].content.contains("Hello from MiniEngine!"));
}

#[tokio::test]
async fn local_engine_search_content() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let messages = engine
        .call_tool("fs", "search_content", serde_json::json!({
            "pattern": "def main",
            "path": fixtures_root().join("tools").to_str().unwrap(),
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("def main"));
}

// ═══════════════════════════════════════════════════════════════════
// Local boundary conditions — 4 tests
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn local_read_nonexistent_file_returns_error_model_message() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let messages = engine
        .call_tool("fs", "read_file", serde_json::json!({
            "path": "/nonexistent/file.xyz",
        }))
        .await
        .unwrap();

    assert_eq!(messages[0].role, "tool");
    assert!(messages[0].content.contains("error"));
    assert!(messages[0].content.contains("file not found"));
}

#[tokio::test]
async fn local_write_creates_parent_dirs() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let tmp = std::env::temp_dir().join("arf_int_deep_dirs");
    let _ = std::fs::remove_dir_all(&tmp);
    let deep_path = tmp.join("a/b/c/d/output.txt");

    let messages = engine
        .call_tool("fs", "write_file", serde_json::json!({
            "path": deep_path.to_str().unwrap(),
            "content": "deeply nested",
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("ok"));
    assert!(deep_path.exists());
    assert_eq!(std::fs::read_to_string(&deep_path).unwrap(), "deeply nested");
}

#[tokio::test]
async fn local_search_no_matches_returns_empty() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let messages = engine
        .call_tool("fs", "search_content", serde_json::json!({
            "pattern": "ZZZZZ_NO_MATCH_ZZZZZ",
            "path": fixtures_root().join("tools").to_str().unwrap(),
        }))
        .await
        .unwrap();

    let content = &messages[0].content;
    assert!(content.contains("matches") && content.contains("[]"),
        "expected empty matches, got: {content}");
}

#[tokio::test]
async fn local_unicode_emoji_newline_roundtrip() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let tmp_dir = std::env::temp_dir().join("arf_int_unicode");
    let _ = std::fs::remove_dir_all(&tmp_dir);
    std::fs::create_dir_all(&tmp_dir).unwrap();
    let file_path = tmp_dir.join("unicode.txt");

    let content = "你好世界 🌍\nLine 2: \"quoted\"\nLine 3: \\backslash\\\nLine 4: \t tab";
    engine
        .call_tool("fs", "write_file", serde_json::json!({
            "path": file_path.to_str().unwrap(),
            "content": content,
        }))
        .await
        .unwrap();

    let messages = engine
        .call_tool("fs", "read_file", serde_json::json!({
            "path": file_path.to_str().unwrap(),
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("你好世界"));
}

// ═══════════════════════════════════════════════════════════════════
// Remote + CodeTidy + MiniEngine — 5 tests
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn remote_engine_codetidy_connect_and_call() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    let messages = engine
        .call_tool("codetidy", "codetidy_uppercase", serde_json::json!({
            "input": "hello world",
        }))
        .await
        .unwrap();

    assert_eq!(messages[0].role, "tool");
    assert!(messages[0].content.contains("HELLO WORLD"));
    assert_eq!(messages[0].name.as_deref(), Some("codetidy_uppercase"));
}

#[tokio::test]
async fn remote_engine_codetidy_base64_roundtrip() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    // Encode
    let messages = engine
        .call_tool("codetidy", "codetidy_base64_encode", serde_json::json!({
            "input": "Roundtrip test 往返测试!",
        }))
        .await
        .unwrap();
    let encoded: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    let raw_output = encoded.as_str().unwrap();
    // CodeTidy appends a footer — extract first line only
    let encoded_str = raw_output.lines().next().unwrap();

    // Decode
    let messages = engine
        .call_tool("codetidy", "codetidy_base64_decode", serde_json::json!({
            "input": encoded_str,
        }))
        .await
        .unwrap();

    let decoded: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    let decoded_text = decoded.as_str().unwrap();
    assert!(decoded_text.contains("Roundtrip test"));
    assert!(decoded_text.contains("往返测试"));
}

#[tokio::test]
async fn remote_engine_codetidy_hash_cross_validate() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    // SHA256("test")
    let messages = engine
        .call_tool("codetidy", "codetidy_hash_generate", serde_json::json!({
            "input": "test",
            "algorithm": "sha256",
        }))
        .await
        .unwrap();
    let sha256: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    assert!(sha256.as_str().unwrap().to_lowercase().starts_with("9f86d081"));

    // MD5("test") — should be different
    let messages = engine
        .call_tool("codetidy", "codetidy_hash_generate", serde_json::json!({
            "input": "test",
            "algorithm": "md5",
        }))
        .await
        .unwrap();
    let md5: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();

    assert_ne!(sha256.as_str().unwrap(), md5.as_str().unwrap());
}

#[tokio::test]
async fn remote_engine_codetidy_url_encode_decode_roundtrip() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    let original = "hello world & more?key=value#fragment";
    let messages = engine
        .call_tool("codetidy", "codetidy_url_encode", serde_json::json!({
            "input": original,
        }))
        .await
        .unwrap();
    let encoded: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    let encoded_str = encoded.as_str().unwrap();
    assert!(encoded_str.contains("%20"));

    let messages = engine
        .call_tool("codetidy", "codetidy_url_decode", serde_json::json!({
            "input": encoded_str,
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("hello world & more"));
}

#[tokio::test]
async fn remote_engine_codetidy_jwt_decode_known_token() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    // JWT with payload: {"sub":"1234567890","name":"John Doe","iat":1516239022}
    let jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c";
    let messages = engine
        .call_tool("codetidy", "codetidy_jwt_decode", serde_json::json!({
            "input": jwt,
        }))
        .await
        .unwrap();

    // JWT decode returns the payload as text; CodeTidy may add a footer
    let decoded: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    let decoded_text = decoded.as_str().unwrap();
    assert!(decoded_text.contains("John Doe"));
    assert!(decoded_text.contains("1234567890"));
}

// ═══════════════════════════════════════════════════════════════════
// Remote boundary conditions — 4 tests
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn remote_nonexistent_tool_returns_model_error() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    let messages = engine
        .call_tool("codetidy", "this_tool_does_not_exist_xyz", serde_json::json!({}))
        .await
        .unwrap();

    assert!(messages[0].content.contains("error"));
    assert!(messages[0].content.contains("not found"));
}

#[tokio::test]
async fn remote_empty_params_tool_works() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    let messages = engine
        .call_tool("codetidy", "codetidy_uuid_generate", serde_json::json!({}))
        .await
        .unwrap();

    let uuid_str = &messages[0].content;
    assert!(uuid_str.len() >= 32, "expected UUID, got: {uuid_str}");
    assert!(uuid_str.contains('-'), "UUID should contain dashes");
}

#[tokio::test]
async fn remote_missing_required_param_errors() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    // hash_generate requires "input" and "algorithm" — omit both
    let messages = engine
        .call_tool("codetidy", "codetidy_hash_generate", serde_json::json!({}))
        .await
        .unwrap();

    assert!(
        messages[0].content.contains("error")
            || messages[0].content.contains("missing")
            || messages[0].content.contains("required"),
        "expected error, got: {}",
        messages[0].content
    );
}

#[tokio::test]
async fn remote_large_input_base64() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    let large_text = "The quick brown fox jumps over the lazy dog. ".repeat(200);
    assert!(large_text.len() > 8000);

    let messages = engine
        .call_tool("codetidy", "codetidy_base64_encode", serde_json::json!({
            "input": large_text,
        }))
        .await
        .unwrap();

    let encoded: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    let raw_output = encoded.as_str().unwrap();
    // CodeTidy adds footer — extract first line only
    let encoded_str = raw_output.lines().next().unwrap();
    assert!(encoded_str.len() > large_text.len());

    // Verify roundtrip
    let messages = engine
        .call_tool("codetidy", "codetidy_base64_decode", serde_json::json!({
            "input": encoded_str,
        }))
        .await
        .unwrap();

    let decoded: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    let decoded_text = decoded.as_str().unwrap();
    assert!(decoded_text.contains("The quick brown fox"));
    assert!(decoded_text.contains("lazy dog"));
}

// ═══════════════════════════════════════════════════════════════════
// Multi-namespace + MiniEngine routing — 2 tests
// ═══════════════════════════════════════════════════════════════════

#[tokio::test]
async fn multi_ns_engine_routes_local_and_remote() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    // Local MCP
    let local_node = Arc::new(McpNode::local("files", fixtures_root()).unwrap());
    local_node.connect(&bus).await.unwrap();
    let (local_nid, local_tools) = wait_for_node_online(&mut rx, "files").await;

    // Remote MCP (CodeTidy)
    let remote_node = McpNode::remote("ct", codetidy_config()).await.unwrap();
    remote_node.connect(&bus).await.unwrap();
    let (remote_nid, remote_tools) = wait_for_node_online(&mut rx, "ct").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("files", local_nid, local_tools);
    engine.register_mcp("ct", remote_nid, remote_tools);

    // Call local tool
    let test_file = fixtures_root().join("tools/read_file/main.py");
    let local_resp = engine
        .call_tool("files", "read_file", serde_json::json!({
            "path": test_file.to_str().unwrap(),
        }))
        .await
        .unwrap();
    assert!(local_resp[0].content.contains("#!/usr/bin/env python3"));

    // Call remote tool — different namespace
    let remote_resp = engine
        .call_tool("ct", "codetidy_uppercase", serde_json::json!({
            "input": "hello",
        }))
        .await
        .unwrap();
    assert!(remote_resp[0].content.contains("HELLO"));

    let nss = engine.namespaces();
    assert!(nss.contains(&"files"));
    assert!(nss.contains(&"ct"));
}

#[tokio::test]
async fn multi_ns_same_tool_name_different_namespace() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root_a = std::env::temp_dir().join(format!("arf_int_ns_a_{id}"));
    let root_b = std::env::temp_dir().join(format!("arf_int_ns_b_{id}"));

    for (root, ns_tag) in [(&root_a, "alpha"), (&root_b, "beta")] {
        let _ = std::fs::remove_dir_all(root);
        let tool_dir = root.join("tools/echo");
        std::fs::create_dir_all(&tool_dir).unwrap();
        std::fs::write(
            tool_dir.join("tool.toml"),
            format!(
                "name = \"echo\"\ndescription = \"Echo in {ns_tag}\"\nruntime = \"python\"\nentrypoint = \"main.py\"\n"
            ),
        )
        .unwrap();
        std::fs::write(
            tool_dir.join("main.py"),
            format!(
                "import sys, json\nparams = json.loads(sys.stdin.read())\nparams[\"ns\"] = \"{ns_tag}\"\nprint(json.dumps(params))\n"
            ),
        )
        .unwrap();
    }

    let node_a = Arc::new(McpNode::local("alpha", root_a).unwrap());
    let node_b = Arc::new(McpNode::local("beta", root_b).unwrap());
    node_a.connect(&bus).await.unwrap();
    node_b.connect(&bus).await.unwrap();

    let (nid_a, tools_a) = wait_for_node_online(&mut rx, "alpha").await;
    let (nid_b, tools_b) = wait_for_node_online(&mut rx, "beta").await;

    let mut engine = MiniEngine::new(&bus, "s1").await;
    engine.register_mcp("alpha", nid_a, tools_a);
    engine.register_mcp("beta", nid_b, tools_b);

    let msg_a = engine
        .call_tool("alpha", "echo", serde_json::json!({"msg": "a"}))
        .await
        .unwrap();
    assert!(msg_a[0].content.contains("\"alpha\""));

    let msg_b = engine
        .call_tool("beta", "echo", serde_json::json!({"msg": "b"}))
        .await
        .unwrap();
    assert!(msg_b[0].content.contains("\"beta\""));
}

// ═══════════════════════════════════════════════════════════════════
// ModelAdapter conversion — 1 test
// ═══════════════════════════════════════════════════════════════════

#[test]
fn tool_result_to_model_message_all_statuses() {
    // success
    let success = ToolResultItem {
        call_id: "c0".into(),
        name: "echo".into(),
        status: "success".into(),
        result: serde_json::json!({"ok": true, "data": 42}),
        error: None,
    };
    let msg = tool_result_to_model_message(&success);
    assert_eq!(msg.role, "tool");
    assert_eq!(msg.tool_call_id.as_deref(), Some("c0"));
    assert_eq!(msg.name.as_deref(), Some("echo"));
    assert!(msg.content.contains("42"));

    // error
    let error = ToolResultItem {
        call_id: "c1".into(),
        name: "fail".into(),
        status: "error".into(),
        result: serde_json::Value::Null,
        error: Some("something went wrong".into()),
    };
    let msg = tool_result_to_model_message(&error);
    assert!(msg.content.contains("error"));
    assert!(msg.content.contains("something went wrong"));

    // cancelled
    let cancelled = ToolResultItem {
        call_id: "c2".into(),
        name: "cancelled_tool".into(),
        status: "cancelled".into(),
        result: serde_json::Value::Null,
        error: Some("cancelled: dependency c1 failed".into()),
    };
    let msg = tool_result_to_model_message(&cancelled);
    assert!(msg.content.contains("error"));
    assert!(msg.content.contains("cancelled"));
    assert!(msg.content.contains("c1"));
}
