//! mcp_local_runtime.rs — Phase 9 task 9.5.4
//!
//! McpNode + LocalRuntime（默认 DAG executor）端到端探查。
//!
//! 4 test cases：
//! 1. local_runtime_capabilities_metadata — LocalRuntime::capabilities() 端到端 OK
//! 2. local_runtime_default_executor_delegate — runtime.execute(call_set, tools) 端到端 OK
//! 3. local_runtime_layer_parallel_concurrent — 2 无依赖 tool_call item 并发执行
//! 4. local_runtime_cascade_cancel_on_failure — 上游 fail 后下游 cascade cancel

mod common;

use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_mcp::discovery::{DiscoveryBackend, FsDiscovery};
use arf_mcp::runtime::{LocalRuntime, RuntimeModule};
use arf_mcp::types::{ToolCallItem, ToolCallSet};

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

fn setup_root(tools: &[(&str, &str, &str)]) -> PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("arf_local_rt_e2e_{id}"));
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

/// Python script that sleeps N ms then echoes params.
fn delay_script(ms: u64) -> String {
    format!(
        "import sys, json, time\nparams=json.loads(sys.stdin.read())\ntime.sleep({ms}/1000.0)\nparams['_delay_ms']={ms}\nprint(json.dumps(params))\n"
    )
}

fn fail_script() -> String {
    "import sys, json\nprint('error: forced failure', file=sys.stderr)\nsys.exit(1)\n".to_string()
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: local_runtime_capabilities_metadata
// ═══════════════════════════════════════════════════════════════════════

#[test]
fn local_runtime_capabilities_metadata() {
    let rt = LocalRuntime;
    let caps = rt.capabilities();
    println!("[test1] LocalRuntime::capabilities() = {}", caps);
    assert_eq!(caps["runtime"], "local");
    assert_eq!(caps["concurrency"], "layer-parallel");
    println!("[test1] LocalRuntime capabilities 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: local_runtime_default_executor_delegate
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn local_runtime_default_executor_delegate() {
    // 直接通过 LocalRuntime::execute（默认 impl）跑一个简单 tool_call_set
    // —— 验 executor::execute 路径端到端 work
    let root = setup_root(&[("echo", &tool_toml("echo"), "import sys,json\np=json.loads(sys.stdin.read())\nprint(json.dumps(p))\n")]);
    let discovery = FsDiscovery::scan(root.clone()).unwrap();
    let tools = discovery.tool_map().clone();

    let rt = LocalRuntime;
    let call_set = ToolCallSet {
        session_id: "test-session".into(),
        calls: vec![ToolCallItem {
            id: "call_0".into(),
            tool: "echo".into(),
            params: serde_json::json!({"hello": "world"}),
            blocked_by: vec![],
            blocking: vec![],
        }],
        timeout_ms: Some(5000),
    };

    let result_set = rt.execute(&call_set, &tools).await;
    println!("[test2] execute() result_set: {:?}", result_set);
    assert_eq!(result_set.results.len(), 1);
    assert_eq!(result_set.results[0].status, "success");
    assert_eq!(result_set.results[0].name, "echo");
    let parsed: serde_json::Value = serde_json::from_value(result_set.results[0].result.clone()).unwrap();
    assert_eq!(parsed["hello"], "world");

    let _ = fs::remove_dir_all(&root);
    println!("[test2] LocalRuntime::execute → executor::execute 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: local_runtime_layer_parallel_concurrent
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn local_runtime_layer_parallel_concurrent() {
    // 2 个 tool，delay 200ms each；如果并发，total ~200ms；如果串行 ~400ms+
    let root = setup_root(&[
        ("slow_a", &tool_toml("slow_a"), &delay_script(200)),
        ("slow_b", &tool_toml("slow_b"), &delay_script(200)),
    ]);
    let discovery = FsDiscovery::scan(root.clone()).unwrap();
    let tools = discovery.tool_map().clone();

    let rt = LocalRuntime;
    let call_set = ToolCallSet {
        session_id: "test-parallel".into(),
        calls: vec![
            ToolCallItem {
                id: "call_a".into(),
                tool: "slow_a".into(),
                params: serde_json::json!({}),
                blocked_by: vec![],
                blocking: vec![],
            },
            ToolCallItem {
                id: "call_b".into(),
                tool: "slow_b".into(),
                params: serde_json::json!({}),
                blocked_by: vec![],
                blocking: vec![],
            },
        ],
        timeout_ms: Some(5000),
    };

    let start = std::time::Instant::now();
    let result_set = rt.execute(&call_set, &tools).await;
    let elapsed = start.elapsed();

    println!("[test3] parallel execute elapsed = {:?}", elapsed);
    println!("[test3] result_set: {} results", result_set.results.len());

    assert_eq!(result_set.results.len(), 2);
    for r in &result_set.results {
        assert_eq!(r.status, "success");
    }
    // 并发执行：总时长应 < 400ms（串行 400ms）
    assert!(
        elapsed < Duration::from_millis(380),
        "并发执行应 < 380ms，实测 {:?}",
        elapsed
    );

    let _ = fs::remove_dir_all(&root);
    println!("[test3] LocalRuntime layer-parallel 并发 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: local_runtime_cascade_cancel_on_failure
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn local_runtime_cascade_cancel_on_failure() {
    // setup: call_0 fail (status="error"), call_1 blocked_by call_0 → cascade cancel
    let root = setup_root(&[
        ("failing", &tool_toml("failing"), &fail_script()),
        ("never_runs", &tool_toml("never_runs"), &delay_script(5000)),  // 长 delay 模拟
    ]);
    let discovery = FsDiscovery::scan(root.clone()).unwrap();
    let tools = discovery.tool_map().clone();

    let rt = LocalRuntime;
    let call_set = ToolCallSet {
        session_id: "test-cascade".into(),
        calls: vec![
            ToolCallItem {
                id: "call_0".into(),
                tool: "failing".into(),
                params: serde_json::json!({}),
                blocked_by: vec![],
                blocking: vec!["call_1".into()],  // call_0 blocks call_1
            },
            ToolCallItem {
                id: "call_1".into(),
                tool: "never_runs".into(),
                params: serde_json::json!({}),
                blocked_by: vec!["call_0".into()],  // call_1 depends on call_0
                blocking: vec![],
            },
        ],
        timeout_ms: Some(8000),
    };

    let start = std::time::Instant::now();
    let result_set = rt.execute(&call_set, &tools).await;
    let elapsed = start.elapsed();

    println!("[test4] cascade cancel elapsed = {:?}", elapsed);
    println!("[test4] result_set: {} results", result_set.results.len());
    for r in &result_set.results {
        println!("[test4]   - call_id={} status={}", r.call_id, r.status);
    }

    assert_eq!(result_set.results.len(), 2);
    // call_0 should be error (forced fail)
    let call_0 = result_set.results.iter().find(|r| r.call_id == "call_0").unwrap();
    assert_eq!(call_0.status, "error", "call_0 应为 error");
    // call_1 should be cancelled (cascade from call_0)
    let call_1 = result_set.results.iter().find(|r| r.call_id == "call_1").unwrap();
    assert_eq!(call_1.status, "cancelled", "call_1 应被 cascade cancel");
    assert!(call_1.error.is_some());
    assert!(call_1.error.as_ref().unwrap().contains("upstream"));

    // cascade 应立即 cancel 不等 call_1 的 5s delay
    assert!(
        elapsed < Duration::from_millis(2000),
        "cascade cancel 应快速，实测 {:?}",
        elapsed
    );

    let _ = fs::remove_dir_all(&root);
    println!("[test4] LocalRuntime cascade cancel 端到端 OK ✓");
}