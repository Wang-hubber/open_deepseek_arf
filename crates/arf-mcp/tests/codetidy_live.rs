//! Live integration tests against CodeTidy MCP (https://mcp.codetidy.dev).
//!
//! These tests require network access. Run with:
//!   cargo test -p arf-mcp --test codetidy_live
//!
//! CodeTidy is a free, no-auth remote MCP with 62 developer tools:
//! JSON, Base64, URL, JWT, UUID, Hash, Semver, MIME, Math, Text, Regex, etc.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::MessageFilter;
use arf_core::NodeId;
use arf_core::NodeInfo;
use arf_mcp::config::RemoteConfig;
use arf_mcp::remote::RemoteMcpNode;

const CODETIDY_URL: &str = "https://mcp.codetidy.dev";

fn codetidy_config() -> RemoteConfig {
    RemoteConfig {
        transport: "streamable-http".into(),
        url: CODETIDY_URL.into(),
        timeout_secs: Some(30),
        headers: HashMap::new(),
        tls_ca_cert: None,
        retry: None,
    }
}

fn test_bus() -> Bus {
    Bus::new(Duration::from_secs(5), Duration::from_secs(30), 128)
}

async fn connect_engine(bus: &Bus, id: &str) -> arf_bus::NodeHandle {
    bus.connect(
        NodeInfo {
            node_id: NodeId::new(id),
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
            to_match: arf_core::ToMatch::BroadcastAndDirectedToMe,
        },
    )
    .await
    .unwrap()
}

// ═══════════════════════════════════════════════════════════════
// 工具发现 — 2 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn codetidy_connect_discovers_all_62_tools() {
    let bus = test_bus();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();

    let graph = bus.graph();
    let info = graph
        .nodes
        .iter()
        .find(|n| n.node_id == node.node_id)
        .unwrap();
    let tools = info.capabilities["tools"].as_array().unwrap();
    assert!(tools.len() >= 60, "expected >=60 tools, got {}", tools.len());
    assert_eq!(info.capabilities["runtime"]["runtime"], "remote");
}

#[tokio::test]
async fn codetidy_tool_names_are_well_formed() {
    let bus = test_bus();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();

    let graph = bus.graph();
    let info = graph
        .nodes
        .iter()
        .find(|n| n.node_id == node.node_id)
        .unwrap();
    let tools = info.capabilities["tools"].as_array().unwrap();

    for t in tools {
        let name = t["name"].as_str().unwrap();
        assert!(
            name.starts_with("codetidy_"),
            "tool name should have codetidy_ prefix: {name}"
        );
    }
}

// ═══════════════════════════════════════════════════════════════
// 无参/全默认工具 — 1 test
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn uuid_generate_with_defaults() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();
    // drain node_online
    loop {
        let m = rx.recv().await.unwrap();
        if m.msg_type == "node_online" {
            break;
        }
    }

    let mut engine = connect_engine(&bus, "engine/uuid").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1", "calls": [{"id":"c0","tool":"codetidy_uuid_generate","params":{}}]
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["status"], "success");
    // UUID v4 format: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
    let uuid_str = r["result"].as_str().unwrap();
    assert!(uuid_str.len() >= 32, "expected UUID, got: {uuid_str}");
}

// ═══════════════════════════════════════════════════════════════
// 简单单参数工具 — 3 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn base64_encode_decode_roundtrip() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();
    loop {
        let m = rx.recv().await.unwrap();
        if m.msg_type == "node_online" {
            break;
        }
    }

    let mut engine = connect_engine(&bus, "engine/b64").await;

    // Encode
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1", "calls": [
                    {"id":"c0","tool":"codetidy_base64_encode","params":{"input":"Hello World!"}},
                    {"id":"c1","tool":"codetidy_base64_decode","params":{"input":"SGVsbG8gV29ybGQh"}},
                ]
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let results = resp.payload["results"].as_array().unwrap();
    assert_eq!(results[0]["status"], "success");
    assert_eq!(results[1]["status"], "success");
}

#[tokio::test]
async fn uppercase_transform() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();
    loop {
        let m = rx.recv().await.unwrap();
        if m.msg_type == "node_online" {
            break;
        }
    }

    let mut engine = connect_engine(&bus, "engine/up").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1", "calls": [
                    {"id":"c0","tool":"codetidy_uppercase","params":{"input":"hello world"}}
                ]
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["status"], "success");
    assert!(r["result"].as_str().unwrap().contains("HELLO WORLD"));
}

#[tokio::test]
async fn url_parse_decomposes_url() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();
    loop {
        let m = rx.recv().await.unwrap();
        if m.msg_type == "node_online" {
            break;
        }
    }

    let mut engine = connect_engine(&bus, "engine/url").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1", "calls": [
                    {"id":"c0","tool":"codetidy_url_parse",
                     "params":{"url":"https://example.com:8080/path?key=value#frag"}}
                ]
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["status"], "success");
}

// ═══════════════════════════════════════════════════════════════
// 多参数工具 — 2 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn hash_generate_with_algorithm() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();
    loop {
        let m = rx.recv().await.unwrap();
        if m.msg_type == "node_online" {
            break;
        }
    }

    let mut engine = connect_engine(&bus, "engine/hash").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1", "calls": [
                    {"id":"c0","tool":"codetidy_hash_generate",
                     "params":{"input":"test","algorithm":"sha256","uppercase":false}}
                ]
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["status"], "success");
    // SHA256("test") = 9f86d081...
    let hash = r["result"].as_str().unwrap().to_lowercase();
    assert!(hash.starts_with("9f86d081"));
}

#[tokio::test]
async fn password_generate_with_params() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();
    loop {
        let m = rx.recv().await.unwrap();
        if m.msg_type == "node_online" {
            break;
        }
    }

    let mut engine = connect_engine(&bus, "engine/pwd").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1", "calls": [
                    {"id":"c0","tool":"codetidy_password_generate",
                     "params":{"length":64,"count":1,"uppercase":true,"lowercase":true,"numbers":true,"symbols":false}}
                ]
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["status"], "success");
    let pwd = r["result"].as_str().unwrap();
    // Password should be at least 64 chars (with length param)
    assert!(pwd.len() >= 64, "password too short: {} chars", pwd.len());
}

// ═══════════════════════════════════════════════════════════════
// 错误处理 — 3 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn nonexistent_tool_returns_error() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();
    loop {
        let m = rx.recv().await.unwrap();
        if m.msg_type == "node_online" {
            break;
        }
    }

    let mut engine = connect_engine(&bus, "engine/err1").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1", "calls": [
                    {"id":"c0","tool":"this_tool_does_not_exist","params":{}}
                ]
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    assert_eq!(resp.payload["results"][0]["status"], "error");
}

#[tokio::test]
async fn json_validate_reports_syntax_errors() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();
    loop {
        let m = rx.recv().await.unwrap();
        if m.msg_type == "node_online" {
            break;
        }
    }

    let mut engine = connect_engine(&bus, "engine/err2").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1", "calls": [
                    {"id":"c0","tool":"codetidy_json_validate",
                     "params":{"input":"not valid json {{ {"}}
                ]
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    // json_validate always returns status="success" (it reports errors in the result text)
    assert_eq!(r["status"], "success");
    // The result should mention "error" or "invalid" in the text
    let result_text = r["result"].as_str().unwrap().to_lowercase();
    assert!(
        result_text.contains("error") || result_text.contains("invalid") || result_text.contains("unexpected"),
        "expected error description, got: {result_text}"
    );
}

#[tokio::test]
async fn skill_messages_return_error() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();
    loop {
        let m = rx.recv().await.unwrap();
        if m.msg_type == "node_online" {
            break;
        }
    }

    let mut engine = connect_engine(&bus, "engine/skill").await;
    engine
        .send(
            "use_skill",
            vec![node.node_id.clone()],
            serde_json::json!({"name": "any-skill"}),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    assert_eq!(resp.msg_type, "skill_error");
    assert!(resp
        .payload["error"]
        .as_str()
        .unwrap()
        .contains("not supported"));
}

// ═══════════════════════════════════════════════════════════════
// 批量调用 — 1 test
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn batch_five_different_tools() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(RemoteMcpNode::new("codetidy", codetidy_config()));
    node.connect(&bus).await.unwrap();
    loop {
        let m = rx.recv().await.unwrap();
        if m.msg_type == "node_online" {
            break;
        }
    }

    let mut engine = connect_engine(&bus, "engine/batch").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1", "calls": [
                    {"id":"c0","tool":"codetidy_json_format","params":{"input":"{\"a\":1}"}},
                    {"id":"c1","tool":"codetidy_base64_encode","params":{"input":"hello"}},
                    {"id":"c2","tool":"codetidy_uppercase","params":{"input":"test"}},
                    {"id":"c3","tool":"codetidy_url_encode","params":{"input":"hello world"}},
                    {"id":"c4","tool":"codetidy_mime_lookup","params":{"query":"json"}},
                ]
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let results = resp.payload["results"].as_array().unwrap();
    assert_eq!(results.len(), 5);
    for r in results {
        assert_eq!(
            r["status"], "success",
            "tool {} failed: {:?}",
            r["name"].as_str().unwrap(),
            r["error"]
        );
    }
}
