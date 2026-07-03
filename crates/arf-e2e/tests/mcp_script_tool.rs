//! mcp_script_tool.rs — Phase 9 task 9.5.7
//!
//! ScriptTool（python/bash/rustc）+ cancel 端到端探查。
//!
//! 4 test cases：
//! 1. script_tool_python_runtime — python runtime 端到端
//! 2. script_tool_bash_runtime — bash runtime 端到端
//! 3. script_tool_rust_runtime_compile_and_run — rust runtime + rustc 编译 + 缓存
//! 4. script_tool_cancel_kills_child — cancel() 真 kill 子进程

mod common;

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use arf_mcp::config::{ScriptRuntime, ToolConfig};
use arf_mcp::script::ScriptTool;
use arf_mcp::tool::Tool;

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

fn setup_tool_dir(name: &str, script_content: &str, ext: &str) -> PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let dir = std::env::temp_dir().join(format!("arf_script_e2e_{name}_{id}"));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let entrypoint = format!("main.{ext}");
    fs::write(dir.join(&entrypoint), script_content).unwrap();
    dir
}

fn make_tool(runtime: ScriptRuntime, name: &str, entrypoint: &str, dir: PathBuf, timeout_ms: Option<u64>) -> ScriptTool {
    let config = ToolConfig {
        name: name.into(),
        description: format!("{name} test tool"),
        runtime,
        entrypoint: entrypoint.into(),
        timeout_ms,
        params_schema: serde_json::json!({"type":"object"}),
    };
    ScriptTool::new(config, dir)
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: script_tool_python_runtime
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn script_tool_python_runtime() {
    let script = "import sys, json\nparams=json.loads(sys.stdin.read())\nprint(json.dumps({'echoed': params, 'lang': 'python'}))\n";
    let dir = setup_tool_dir("py", script, "py");
    let tool = make_tool(ScriptRuntime::Python, "py_echo", "main.py", dir.clone(), Some(5000));

    let result = tool
        .execute(serde_json::json!({"x": 42}))
        .await
        .expect("execute python");
    println!("[test1] python result: {:?}", result);
    let parsed: serde_json::Value = serde_json::from_value(result).unwrap();
    assert_eq!(parsed["lang"], "python");
    assert_eq!(parsed["echoed"]["x"], 42);

    let _ = fs::remove_dir_all(&dir);
    println!("[test1] ScriptTool Python runtime 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: script_tool_bash_runtime
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn script_tool_bash_runtime() {
    // bash script: read stdin, parse JSON with grep/sed (避免 jq 依赖), 回传
    let script = r#"#!/bin/bash
read params
echo "{\"echoed\":$params,\"lang\":\"bash\"}"
"#;
    let dir = setup_tool_dir("bash", script, "sh");
    let tool = make_tool(ScriptRuntime::Bash, "bash_echo", "main.sh", dir.clone(), Some(5000));

    let result = tool
        .execute(serde_json::json!({"y": 99}))
        .await
        .expect("execute bash");
    println!("[test2] bash result: {:?}", result);
    let parsed: serde_json::Value = serde_json::from_value(result).unwrap();
    assert_eq!(parsed["lang"], "bash");
    assert_eq!(parsed["echoed"]["y"], 99);

    let _ = fs::remove_dir_all(&dir);
    println!("[test2] ScriptTool Bash runtime 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: script_tool_rust_runtime_compile_and_run
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn script_tool_rust_runtime_compile_and_run() {
    // 简易 rust 程序：读 stdin → 解析 {"x":i32} → 输出 {"rust":{"x":x*2}}
    // 注：避免使用 serde（compile 慢），手写 JSON parse
    let rust_src = r#"
use std::io::Read;
fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).unwrap();
    // 提取 "x":NUMBER 部分
    let needle = "\"x\":";
    if let Some(i) = s.find(needle) {
        let rest = &s[i+needle.len()..];
        let end = rest.find(|c: char| !c.is_ascii_digit() && c != '-').unwrap_or(rest.len());
        let n: i64 = rest[..end].parse().unwrap_or(0);
        println!("{{\"rust\":{{\"doubled\":{}}}}}", n * 2);
    } else {
        println!("{{\"rust\":\"parse-fail\"}}");
    }
}
"#;
    let dir = setup_tool_dir("rust", rust_src, "rs");
    let tool = make_tool(ScriptRuntime::Rust, "rust_doubler", "main.rs", dir.clone(), Some(30000));

    // 第一次执行 → rustc 编译 → 跑 binary
    let start = std::time::Instant::now();
    let result = tool
        .execute(serde_json::json!({"x": 21}))
        .await
        .expect("execute rust");
    let compile_elapsed = start.elapsed();
    println!("[test3] rust (first call, includes compile) result: {:?} elapsed={:?}", result, compile_elapsed);
    let parsed: serde_json::Value = serde_json::from_value(result).unwrap();
    assert_eq!(parsed["rust"]["doubled"], 42, "21 * 2 == 42");

    // 第二次执行 → 跳过编译（mtime 没变）
    let start = std::time::Instant::now();
    let result2 = tool
        .execute(serde_json::json!({"x": 100}))
        .await
        .expect("execute rust cached");
    let cached_elapsed = start.elapsed();
    println!("[test3] rust (second call, cached binary) result: {:?} elapsed={:?}", result2, cached_elapsed);
    let parsed2: serde_json::Value = serde_json::from_value(result2).unwrap();
    assert_eq!(parsed2["rust"]["doubled"], 200);

    // 缓存应明显快（< 100ms vs 编译 1s+）
    println!("[test3] cached elapsed = {:?} (compile elapsed = {:?})", cached_elapsed, compile_elapsed);
    assert!(cached_elapsed < Duration::from_millis(500), "cached 执行应 < 500ms");

    let _ = fs::remove_dir_all(&dir);
    println!("[test3] ScriptTool Rust runtime + rustc 缓存 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: script_tool_cancel_kills_child
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn script_tool_cancel_kills_child() {
    // 长跑 tool：sleep 60s
    let script = "import time, sys\ntime.sleep(60)\nprint(json.dumps({\"ok\":True}))\n";
    let dir = setup_tool_dir("cancel", script, "py");
    // 不设 timeout（验证 cancel 路径独立）—— 设短 timeout 模拟 cascade cancel
    let tool = Arc::new(make_tool(ScriptRuntime::Python, "long_runner", "main.py", dir.clone(), None));

    let tool_clone = tool.clone();
    let handle = tokio::spawn(async move {
        tool_clone.execute(serde_json::json!({})).await
    });

    // 给点时间让子进程启动
    tokio::time::sleep(Duration::from_millis(500)).await;
    println!("[test4] 调用 cancel() 触发子进程 kill");

    let cancel_start = std::time::Instant::now();
    tool.cancel().await;

    // 等 execute 返回
    let result = tokio::time::timeout(Duration::from_secs(3), handle)
        .await
        .expect("execute 不应在 cancel 后超 3s")
        .expect("task join")
        .expect_err("execute 应返回 Err");
    let cancel_elapsed = cancel_start.elapsed();
    println!("[test4] execute 返回: {:?} (elapsed after cancel = {:?})", result, cancel_elapsed);

    // cancel 应立即返回 Err("cancelled")，而不是等 60s sleep
    assert!(cancel_elapsed < Duration::from_millis(2000), "cancel 应快速，实测 {:?}", cancel_elapsed);
    assert!(result.message.to_lowercase().contains("cancel") || result.message.contains("timeout"));

    let _ = fs::remove_dir_all(&dir);
    println!("[test4] ScriptTool cancel 端到端 OK ✓");
}