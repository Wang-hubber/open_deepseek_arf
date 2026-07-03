//! mcp_custom_runtime.rs — Phase 9 task 9.5.6
//!
//! McpNode + 自定义 RuntimeModule（sandbox/retry/metrics 场景）端到端探查。
//!
//! 4 test cases：
//! 1. custom_runtime_capabilities_custom_metadata — 自定义 capabilities JSON
//! 2. custom_runtime_default_execute_delegate — 不 override execute → 默认 delegate
//! 3. custom_runtime_override_execute_with_retry — override execute + retry 逻辑
//! 4. custom_runtime_via_local_with_runtime_in_mcp_node — McpNode::local_with_runtime + NodeInfo caps

mod common;

use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Duration;

use arf_bus::Bus;
use arf_mcp::discovery::{DiscoveryBackend, FsDiscovery};
use arf_mcp::runtime::RuntimeModule;
use arf_mcp::types::{ToolCallItem, ToolCallSet, ToolResultSet};
use async_trait::async_trait;
use serde_json::Value;

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

fn setup_root(tools: &[(&str, &str, &str)]) -> PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("arf_custom_rt_e2e_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    for (name, toml, script) in tools {
        let tool_dir = root.join("tools").join(name);
        fs::create_dir_all(&tool_dir).unwrap();
        let mut f = fs::File::create(tool_dir.join("tool.toml")).unwrap();
        f.write_all(toml.as_bytes()).unwrap();
        fs::write(tool_dir.join("main.py"), script).unwrap();
    }
    root
}

fn tool_toml(name: &str) -> String {
    format!(
        r#"name = "{name}"
description = "{name} tool"
runtime = "python"
entrypoint = "main.py"
"#
    )
}

// ═══════════════════════════════════════════════════════════════════════
// CustomRuntime implementations
// ═══════════════════════════════════════════════════════════════════════

/// CountingRuntime — 不 override execute，只 override capabilities（带计数器 metadata）
struct CountingRuntime {
    call_count: Arc<AtomicU32>,
}

#[async_trait]
impl RuntimeModule for CountingRuntime {
    fn capabilities(&self) -> Value {
        serde_json::json!({
            "runtime": "custom-counting",
            "concurrency": "sequential",
            "metadata": {"kind": "counting-runtime", "version": "1.0"}
        })
    }
    async fn execute(
        &self,
        call_set: &ToolCallSet,
        tools: &HashMap<String, Arc<dyn arf_mcp::tool::Tool>>,
    ) -> ToolResultSet {
        self.call_count.fetch_add(1, Ordering::SeqCst);
        // 默认 delegate executor
        arf_mcp::executor::execute(call_set, tools).await
    }
}

/// RetryRuntime — override execute，每个 call 失败后自动重试 N 次
struct RetryRuntime {
    max_retries: u32,
}

#[async_trait]
impl RuntimeModule for RetryRuntime {
    fn capabilities(&self) -> Value {
        serde_json::json!({
            "runtime": "custom-retry",
            "max_retries": self.max_retries
        })
    }
    async fn execute(
        &self,
        call_set: &ToolCallSet,
        tools: &HashMap<String, Arc<dyn arf_mcp::tool::Tool>>,
    ) -> ToolResultSet {
        // 简化：所有 call 跑 retry → 默认 executor
        // 单 tool 失败 case 通过 max_retries 决定是否再调一次
        let mut last = arf_mcp::executor::execute(call_set, tools).await;
        for _ in 0..self.max_retries {
            if last.results.iter().all(|r| r.status == "success") {
                return last;
            }
            last = arf_mcp::executor::execute(call_set, tools).await;
        }
        last
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: custom_runtime_capabilities_custom_metadata
// ═══════════════════════════════════════════════════════════════════════

#[test]
fn custom_runtime_capabilities_custom_metadata() {
    let rt = CountingRuntime { call_count: Arc::new(AtomicU32::new(0)) };
    let caps = rt.capabilities();
    println!("[test1] CountingRuntime::capabilities() = {}", caps);
    assert_eq!(caps["runtime"], "custom-counting");
    assert_eq!(caps["concurrency"], "sequential");
    assert_eq!(caps["metadata"]["kind"], "counting-runtime");
    assert_eq!(caps["metadata"]["version"], "1.0");
    println!("[test1] 自定义 capabilities metadata 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: custom_runtime_default_execute_delegate
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn custom_runtime_default_execute_delegate() {
    let root = setup_root(&[
        ("echo_a", &tool_toml("echo_a"), "import sys,json\np=json.loads(sys.stdin.read())\nprint(json.dumps(p))\n"),
        ("echo_b", &tool_toml("echo_b"), "import sys,json\np=json.loads(sys.stdin.read())\nprint(json.dumps(p))\n"),
    ]);
    let discovery = FsDiscovery::scan(root.clone()).unwrap();
    let tools = discovery.tool_map().clone();

    let rt = CountingRuntime { call_count: Arc::new(AtomicU32::new(0)) };
    let call_set = ToolCallSet {
        session_id: "test-rt".into(),
        calls: vec![
            ToolCallItem { id: "call_0".into(), tool: "echo_a".into(), params: serde_json::json!({"x":1}), blocked_by: vec![], blocking: vec![] },
            ToolCallItem { id: "call_1".into(), tool: "echo_b".into(), params: serde_json::json!({"y":2}), blocked_by: vec![], blocking: vec![] },
        ],
        timeout_ms: Some(5000),
    };

    let result_set = rt.execute(&call_set, &tools).await;
    println!("[test2] result_set: {} results", result_set.results.len());
    assert_eq!(result_set.results.len(), 2);
    for r in &result_set.results {
        assert_eq!(r.status, "success");
    }
    assert_eq!(rt.call_count.load(Ordering::SeqCst), 1, "execute 调 1 次");

    let _ = fs::remove_dir_all(&root);
    println!("[test2] 自定义 runtime delegate executor 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: custom_runtime_override_execute_with_retry
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn custom_runtime_override_execute_with_retry() {
    // success-only tools → 1 次 execute 即可 → retry 不再触发
    let root = setup_root(&[
        ("echo", &tool_toml("echo"), "import sys,json\np=json.loads(sys.stdin.read())\nprint(json.dumps(p))\n"),
    ]);
    let discovery = FsDiscovery::scan(root.clone()).unwrap();
    let tools = discovery.tool_map().clone();

    let rt = RetryRuntime { max_retries: 3 };
    let call_set = ToolCallSet {
        session_id: "test-retry".into(),
        calls: vec![ToolCallItem { id: "call_0".into(), tool: "echo".into(), params: serde_json::json!({"v":1}), blocked_by: vec![], blocking: vec![] }],
        timeout_ms: Some(5000),
    };

    let result_set = rt.execute(&call_set, &tools).await;
    println!("[test3] retry result_set: {} results", result_set.results.len());
    assert_eq!(result_set.results.len(), 1);
    assert_eq!(result_set.results[0].status, "success");
    let parsed: Value = serde_json::from_value(result_set.results[0].result.clone()).unwrap();
    assert_eq!(parsed["v"], 1);

    let _ = fs::remove_dir_all(&root);
    println!("[test3] 自定义 runtime override execute (retry) 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: custom_runtime_via_local_with_runtime_in_mcp_node
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn custom_runtime_via_local_with_runtime_in_mcp_node() {
    let root = setup_root(&[("echo", &tool_toml("echo"), "import sys,json\np=json.loads(sys.stdin.read())\nprint(json.dumps(p))\n")]);

    let rt: Box<dyn RuntimeModule> = Box::new(CountingRuntime {
        call_count: Arc::new(AtomicU32::new(0)),
    });

    let node = arf_mcp::McpNode::local_with_runtime("test-custom-rt", root.clone(), rt)
        .expect("McpNode::local_with_runtime");
    println!("[test4] McpNode::local_with_runtime 创建成功");

    let bus = Arc::new(Bus::new(
        Duration::from_secs(30),
        Duration::from_secs(60),
        1024,
    ));
    // subscribe 在 connect 之前，确保收到 node_online
    let mut sub = bus.subscribe();
    node.connect(&bus).await.expect("McpNode::connect");

    // 验 NodeInfo 含自定义 caps
    let mut found_node_info = false;
    for _ in 0..5 {
        match tokio::time::timeout(Duration::from_millis(500), sub.recv()).await {
            Ok(Ok(m)) if m.msg_type == "node_online" => {
                println!("[test4] node_online payload: {}", m.payload);
                let caps = &m.payload["capabilities"];
                let rt_caps = &caps["runtime"];
                if rt_caps["runtime"] == "custom-counting" {
                    println!("[test4] NodeInfo.runtime.runtime == custom-counting ✓");
                    found_node_info = true;
                    break;
                }
            }
            _ => {}
        }
    }
    assert!(found_node_info, "应收到 node_online 且含自定义 caps");

    let _ = fs::remove_dir_all(&root);
    println!("[test4] McpNode::local_with_runtime + bus connect 端到端 OK ✓");
}