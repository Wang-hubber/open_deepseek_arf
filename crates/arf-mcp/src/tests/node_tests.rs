use std::fs;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo};

use crate::node::LocalMcpNode;

// ── Helpers ─────────────────────────────────────────────────────────

const ECHO_TOML: &str = r#"name = "echo"
description = "Echo tool"
runtime = "python"
entrypoint = "main.py"
"#;

const ECHO_PY: &str = "import sys, json\nparams = json.loads(sys.stdin.read())\nprint(json.dumps(params))\n";

const FAIL_TOML: &str = r#"name = "fail"
description = "Always fails"
runtime = "python"
entrypoint = "main.py"
"#;

const FAIL_PY: &str = "import sys\nsys.stderr.write('intentional failure')\nsys.exit(1)\n";

fn tool<'a>(name: &'a str, toml: &'a str, script: &'a str) -> (&'a str, &'a str, &'a str) {
    (name, toml, script)
}

fn setup_root(tools: &[(&str, &str, &str)]) -> std::path::PathBuf {
    let id = super::TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let root = std::env::temp_dir().join(format!("arf_mcp_int_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    for (name, toml, script) in tools {
        let tool_dir = root.join("tools").join(name);
        fs::create_dir_all(&tool_dir).unwrap();
        fs::write(tool_dir.join("tool.toml"), toml).unwrap();
        fs::write(tool_dir.join("main.py"), script).unwrap();
    }
    root
}

fn echo_call(id: &str, params: serde_json::Value) -> serde_json::Value {
    serde_json::json!({ "id": id, "tool": "echo", "params": params })
}

fn call_with_deps(id: &str, tool: &str, params: serde_json::Value, blocked_by: &[&str], blocking: &[&str]) -> serde_json::Value {
    serde_json::json!({ "id": id, "tool": tool, "params": params, "blocked_by": blocked_by, "blocking": blocking })
}

/// Connect a fake engine node to the Bus. Engine MUST be a registered Bus node
/// for directed responses (tool_result_set etc.) to be deliverable.
async fn connect_engine(bus: &Bus, id: &str, result_filter: bool) -> arf_bus::NodeHandle {
    let types = if result_filter {
        Some(vec!["tool_result_set".into(), "skill_loaded".into(), "skill_resource_loaded".into(), "skill_script_result".into()])
    } else {
        None
    };
    bus.connect(
        NodeInfo { node_id: NodeId::new(id), node_type: "engine".into(), capabilities: serde_json::json!({}), online_since: 0 },
        MessageFilter { types, to_match: arf_core::ToMatch::All },
    ).await.unwrap()
}

// ═══════════════════════════════════════════════════════════════
// 注册与发现 — 2 tests
// ═══════════════════════════════════════════════════════════════

// [集成] 连接 Bus → node_online 广播携带 tools + runtime
#[tokio::test]
async fn node_online_contains_capabilities() {
    let root = setup_root(&[tool("echo", ECHO_TOML, ECHO_PY)]);
    let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16);

    let mut rx = bus.subscribe();
    let node = Arc::new(LocalMcpNode::new("test", root).unwrap());
    node.connect(&bus).await.unwrap();

    // Read raw subscription for node_online (broadcast, no engine needed)
    let msg = tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            let m = rx.recv().await.unwrap();
            if m.msg_type != "heartbeat_request" { return m; }
        }
    }).await.unwrap();

    assert_eq!(msg.msg_type, "node_online");
    let caps = &msg.payload["capabilities"];
    assert_eq!(caps["runtime"]["runtime"], "local");
    assert_eq!(caps["tools"][0]["name"], "echo");
}

// [集成] bus.graph() 能看到在线节点
#[tokio::test]
async fn bus_graph_includes_connected_node() {
    let root = setup_root(&[tool("echo", ECHO_TOML, ECHO_PY)]);
    let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16);

    let mut rx = bus.subscribe();
    let node = Arc::new(LocalMcpNode::new("test", root).unwrap());
    node.connect(&bus).await.unwrap();
    // drain node_online
    loop { let m = rx.recv().await.unwrap(); if m.msg_type == "node_online" { break; } }

    let graph = bus.graph();
    let info = graph.nodes.iter().find(|n| n.node_id == node.node_id).unwrap();
    assert_eq!(info.node_type, "mcp");
    assert_eq!(info.capabilities["tools"][0]["name"], "echo");
}

// ═══════════════════════════════════════════════════════════════
// 执行 — 单工具 — 2 tests
// ═══════════════════════════════════════════════════════════════

// [集成] 单工具调用 → success + name 回填 + session_id 保留
#[tokio::test]
async fn single_tool_success() {
    let root = setup_root(&[tool("echo", ECHO_TOML, ECHO_PY)]);
    let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16);

    let mut rx = bus.subscribe();
    let node = Arc::new(LocalMcpNode::new("test", root).unwrap());
    node.connect(&bus).await.unwrap();
    loop { let m = rx.recv().await.unwrap(); if m.msg_type == "node_online" { break; } }

    let mut engine = connect_engine(&bus, "engine/s1", true).await;

    engine.send("tool_call_set", vec![node.node_id.clone()],
        serde_json::json!({"session_id": "s1", "calls": [echo_call("c0", serde_json::json!({"msg":"hi"}))]}),
    ).await.unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["call_id"], "c0");
    assert_eq!(r["name"], "echo");
    assert_eq!(r["status"], "success");
    assert_eq!(r["result"]["msg"], "hi");
    assert!(r["error"].is_null());
    assert_eq!(resp.payload["session_id"], "s1");
}

// [集成] tool 不存在 → error
#[tokio::test]
async fn tool_not_found_returns_error() {
    let root = setup_root(&[tool("echo", ECHO_TOML, ECHO_PY)]);
    let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16);
    let mut rx = bus.subscribe();
    let node = Arc::new(LocalMcpNode::new("test", root).unwrap());
    node.connect(&bus).await.unwrap();
    loop { let m = rx.recv().await.unwrap(); if m.msg_type == "node_online" { break; } }

    let mut engine = connect_engine(&bus, "engine/s1", true).await;
    engine.send("tool_call_set", vec![node.node_id.clone()],
        serde_json::json!({"session_id": "s1", "calls": [{"id":"c0","tool":"ghost","params":{}}]}),
    ).await.unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["status"], "error");
    assert!(r["error"].as_str().unwrap().contains("not found"));
}

// ═══════════════════════════════════════════════════════════════
// 执行 — 并发 — 1 test
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn two_independent_concurrent_calls() {
    let root = setup_root(&[tool("echo", ECHO_TOML, ECHO_PY)]);
    let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16);
    let mut rx = bus.subscribe();
    let node = Arc::new(LocalMcpNode::new("test", root).unwrap());
    node.connect(&bus).await.unwrap();
    loop { let m = rx.recv().await.unwrap(); if m.msg_type == "node_online" { break; } }

    let mut engine = connect_engine(&bus, "engine/s1", true).await;
    engine.send("tool_call_set", vec![node.node_id.clone()],
        serde_json::json!({"session_id": "s1", "calls": [
            echo_call("c0", serde_json::json!({"n":1})),
            echo_call("c1", serde_json::json!({"n":2})),
        ]}),
    ).await.unwrap();

    let resp = engine.recv().await.unwrap();
    assert_eq!(resp.payload["results"].as_array().unwrap().len(), 2);
    assert!(resp.payload["results"].as_array().unwrap().iter().all(|r| r["status"] == "success"));
}

// ═══════════════════════════════════════════════════════════════
// 执行 — DAG 依赖 — 2 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn dependency_chain_serialized() {
    let root = setup_root(&[tool("echo", ECHO_TOML, ECHO_PY)]);
    let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16);
    let mut rx = bus.subscribe();
    let node = Arc::new(LocalMcpNode::new("test", root).unwrap());
    node.connect(&bus).await.unwrap();
    loop { let m = rx.recv().await.unwrap(); if m.msg_type == "node_online" { break; } }

    let mut engine = connect_engine(&bus, "engine/s1", true).await;
    engine.send("tool_call_set", vec![node.node_id.clone()],
        serde_json::json!({"session_id": "s1", "calls": [
            call_with_deps("c0","echo",serde_json::json!({"step":1}), &[], &["c1"]),
            call_with_deps("c1","echo",serde_json::json!({"step":2}), &["c0"], &[]),
        ]}),
    ).await.unwrap();

    let resp = engine.recv().await.unwrap();
    let results = resp.payload["results"].as_array().unwrap();
    assert!(results.iter().all(|r| r["status"] == "success"));
}

#[tokio::test]
async fn cascade_cancel_on_error() {
    let root = setup_root(&[
        tool("fail", FAIL_TOML, FAIL_PY),
        tool("echo", ECHO_TOML, ECHO_PY),
    ]);
    let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16);
    let mut rx = bus.subscribe();
    let node = Arc::new(LocalMcpNode::new("test", root).unwrap());
    node.connect(&bus).await.unwrap();
    loop { let m = rx.recv().await.unwrap(); if m.msg_type == "node_online" { break; } }

    let mut engine = connect_engine(&bus, "engine/s1", true).await;
    engine.send("tool_call_set", vec![node.node_id.clone()],
        serde_json::json!({"session_id": "s1", "calls": [
            call_with_deps("c0","fail",serde_json::json!({}), &[], &["c1"]),
            call_with_deps("c1","echo",serde_json::json!({"ok":true}), &["c0"], &[]),
        ]}),
    ).await.unwrap();

    let resp = engine.recv().await.unwrap();
    let results = resp.payload["results"].as_array().unwrap();
    assert_eq!(results[0]["status"], "error");
    assert_eq!(results[0]["name"], "fail");
    assert_eq!(results[1]["status"], "cancelled");
    assert_eq!(results[1]["name"], "echo");
    assert!(results[1]["error"].as_str().unwrap().contains("c0"));
}

// ═══════════════════════════════════════════════════════════════
// 边界 — 2 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn invalid_payload_returns_error_not_panic() {
    let root = setup_root(&[tool("echo", ECHO_TOML, ECHO_PY)]);
    let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16);
    let mut rx = bus.subscribe();
    let node = Arc::new(LocalMcpNode::new("test", root).unwrap());
    node.connect(&bus).await.unwrap();
    loop { let m = rx.recv().await.unwrap(); if m.msg_type == "node_online" { break; } }

    let mut engine = connect_engine(&bus, "engine/s1", true).await;
    engine.send("tool_call_set", vec![node.node_id.clone()],
        serde_json::json!({"not_a_valid_tool_call_set": true}),
    ).await.unwrap();

    let resp = engine.recv().await.unwrap();
    assert_eq!(resp.payload["results"][0]["status"], "error");
}

#[tokio::test]
async fn empty_call_set_returns_empty_result() {
    let root = setup_root(&[tool("echo", ECHO_TOML, ECHO_PY)]);
    let bus = Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16);
    let mut rx = bus.subscribe();
    let node = Arc::new(LocalMcpNode::new("test", root).unwrap());
    node.connect(&bus).await.unwrap();
    loop { let m = rx.recv().await.unwrap(); if m.msg_type == "node_online" { break; } }

    let mut engine = connect_engine(&bus, "engine/s1", true).await;
    engine.send("tool_call_set", vec![node.node_id.clone()],
        serde_json::json!({"session_id": "s1", "calls": []}),
    ).await.unwrap();

    let resp = engine.recv().await.unwrap();
    assert!(resp.payload["results"].as_array().unwrap().is_empty());
    assert_eq!(resp.payload["session_id"], "s1");
}
// (patched via heredoc for debug)
