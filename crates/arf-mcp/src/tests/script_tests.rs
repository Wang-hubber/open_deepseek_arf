use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use crate::config::{ScriptRuntime, ToolConfig};
use crate::script::ScriptTool;
use crate::tool::Tool;

static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

/// Create a temp directory, write a script file into it, return (tool_dir, config).
/// Each invocation gets a unique directory (counter-based) to avoid races
/// when tests run in parallel.
fn setup_script_tool(
    name: &str,
    runtime: ScriptRuntime,
    script_content: &str,
    timeout_ms: Option<u64>,
) -> (ScriptTool, PathBuf) {
    let id = TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let temp = std::env::temp_dir().join(format!("arf_mcp_test_{name}_{id}"));
    let _ = fs::remove_dir_all(&temp);
    fs::create_dir_all(&temp).unwrap();

    let entrypoint = match runtime {
        ScriptRuntime::Python => {
            let p = temp.join("main.py");
            fs::write(&p, script_content).unwrap();
            "main.py".to_string()
        }
        ScriptRuntime::Bash => {
            let p = temp.join("main.sh");
            fs::write(&p, script_content).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&p, fs::Permissions::from_mode(0o755)).unwrap();
            }
            "main.sh".to_string()
        }
        ScriptRuntime::Rust => {
            let entry = "main.rs";
            let p = temp.join(entry);
            fs::write(&p, script_content).unwrap();
            entry.to_string()
        }
    };

    let config = ToolConfig {
        name: name.into(),
        description: format!("Test tool: {name}"),
        runtime,
        entrypoint,
        timeout_ms,
        params_schema: serde_json::json!({"type": "object"}),
    };

    (ScriptTool::new(config, temp.clone()), temp)
}

impl Drop for Cleanup {
    fn drop(&mut self) {
        if self.0.exists() {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
}

struct Cleanup(PathBuf);

/// Shortcut: Python echo script that returns params as-is.
fn python_echo_tool() -> (ScriptTool, Cleanup) {
    let (tool, dir) = setup_script_tool(
        "echo_py",
        ScriptRuntime::Python,
        "import sys, json\nparams = json.loads(sys.stdin.read())\nprint(json.dumps(params))\n",
        Some(5000),
    );
    (tool, Cleanup(dir))
}

/// Shortcut: Bash echo script that returns params as-is.
fn bash_echo_tool() -> (ScriptTool, Cleanup) {
    let (tool, dir) = setup_script_tool(
        "echo_sh",
        ScriptRuntime::Bash,
        "#!/bin/bash\ninput=$(cat)\necho \"$input\"\n",
        Some(5000),
    );
    (tool, Cleanup(dir))
}

/// Shortcut: Rust echo program — reads stdin, writes to stdout.
fn rust_echo_tool() -> (ScriptTool, Cleanup) {
    let (tool, dir) = setup_script_tool(
        "echo_rs",
        ScriptRuntime::Rust,
        "use std::io::Read;\nfn main() {\n    let mut input = String::new();\n    std::io::stdin().read_to_string(&mut input).unwrap();\n    print!(\"{}\", input);\n}\n",
        Some(30000),
    );
    (tool, Cleanup(dir))
}

// ═══════════════════════════════════════════════════════════════
// ScriptTool 构造 — 3 tests
// ═══════════════════════════════════════════════════════════════

// [构造] ScriptTool 返回正确的元数据
#[test]
fn script_tool_metadata() {
    let config = ToolConfig {
        name: "test_tool".into(),
        description: "A test tool".into(),
        runtime: ScriptRuntime::Python,
        entrypoint: "main.py".into(),
        timeout_ms: Some(10000),
        params_schema: serde_json::json!({"type": "object", "properties": {"x": {"type": "integer"}}}),
    };
    let tool = ScriptTool::new(config, PathBuf::from("/tmp/test_tool"));
    assert_eq!(tool.name(), "test_tool");
    assert_eq!(tool.description(), "A test tool");
    assert_eq!(tool.parameters_schema()["type"], "object");
}

// [构造] timeout_ms = None 时正常构造
#[test]
fn script_tool_no_timeout() {
    let config = ToolConfig {
        name: "no_timeout".into(),
        description: "No timeout".into(),
        runtime: ScriptRuntime::Bash,
        entrypoint: "main.sh".into(),
        timeout_ms: None,
        params_schema: serde_json::Value::Null,
    };
    let tool = ScriptTool::new(config, PathBuf::from("/tmp/t"));
    assert!(tool.name() == "no_timeout");
}

// [边界] 空 name/description 不 panic
#[test]
fn script_tool_empty_strings() {
    let config = ToolConfig {
        name: "".into(),
        description: "".into(),
        runtime: ScriptRuntime::Bash,
        entrypoint: "".into(),
        timeout_ms: None,
        params_schema: serde_json::Value::Null,
    };
    let tool = ScriptTool::new(config, PathBuf::new());
    assert_eq!(tool.name(), "");
    assert_eq!(tool.description(), "");
}

// ═══════════════════════════════════════════════════════════════
// ScriptTool execute — 13 tests
// ═══════════════════════════════════════════════════════════════

// [方法] Python 脚本执行：echo params 回显
#[tokio::test]
async fn execute_python_echo() {
    let (tool, _cleanup) = python_echo_tool();
    let result = tool
        .execute(serde_json::json!({"message": "hello", "count": 42}))
        .await
        .unwrap();
    assert_eq!(result["message"], "hello");
    assert_eq!(result["count"], 42);
}

// [方法] Bash 脚本执行：echo params 回显
#[tokio::test]
async fn execute_bash_echo() {
    let (tool, _cleanup) = bash_echo_tool();
    let result = tool
        .execute(serde_json::json!({"msg": "bash works"}))
        .await
        .unwrap();
    assert_eq!(result["msg"], "bash works");
}

// [方法] 脚本 exit code 非 0 → 返回 error
#[tokio::test]
async fn execute_script_nonzero_exit() {
    let script = "import sys\nsys.exit(1)\n";
    let (tool, _cleanup) = setup_script_tool("failer", ScriptRuntime::Python, script, Some(5000));
    let result = tool.execute(serde_json::json!({})).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().message.contains("exit code"));
}

// [方法] 脚本输出非 JSON → 返回 parse error
#[tokio::test]
async fn execute_script_invalid_json_output() {
    let script = "print('not json')\n";
    let (tool, _cleanup) =
        setup_script_tool("badjson", ScriptRuntime::Python, script, Some(5000));
    let result = tool.execute(serde_json::json!({})).await;
    assert!(result.is_err());
}

// [方法] stderr 写入不影响成功结果（只检查 exit code 和 stdout）
#[tokio::test]
async fn execute_script_with_stderr_output() {
    let script = "import sys, json\nparams = json.loads(sys.stdin.read())\nsys.stderr.write('warning: deprecated')\nprint(json.dumps(params))\n";
    let (tool, _cleanup) =
        setup_script_tool("stderr_warn", ScriptRuntime::Python, script, Some(5000));
    let result = tool
        .execute(serde_json::json!({"ok": true}))
        .await
        .unwrap();
    assert_eq!(result["ok"], true);
}

// [方法] 空 JSON 对象 {} 作为参数
#[tokio::test]
async fn execute_empty_params() {
    let (tool, _cleanup) = python_echo_tool();
    let result = tool.execute(serde_json::json!({})).await.unwrap();
    assert!(result.is_object());
}

// [方法] null 参数
#[tokio::test]
async fn execute_null_params() {
    let (tool, _cleanup) = python_echo_tool();
    let result = tool.execute(serde_json::Value::Null).await.unwrap();
    assert!(result.is_null());
}

// [边界] 不存在的入口脚本 → spawn error
#[tokio::test]
async fn execute_nonexistent_entrypoint() {
    let config = ToolConfig {
        name: "ghost".into(),
        description: "Does not exist".into(),
        runtime: ScriptRuntime::Python,
        entrypoint: "nope.py".into(),
        timeout_ms: Some(5000),
        params_schema: serde_json::Value::Null,
    };
    let tool = ScriptTool::new(config, PathBuf::from("/tmp/nonexistent_dir_xyz"));
    let result = tool.execute(serde_json::json!({})).await;
    assert!(result.is_err());
}

// ═══════════════════════════════════════════════════════════════
// ScriptTool Rust — 5 tests
// ═══════════════════════════════════════════════════════════════

// [方法] Rust echo 编译 + 执行 + params 回显
#[tokio::test]
async fn execute_rust_echo() {
    let (tool, _cleanup) = rust_echo_tool();
    let result = tool
        .execute(serde_json::json!({"lang": "rust", "n": 1}))
        .await
        .unwrap();
    assert_eq!(result["lang"], "rust");
    assert_eq!(result["n"], 1);
}

// [方法] Rust 编译错误 → 返回 error
#[tokio::test]
async fn execute_rust_compile_error() {
    let script = "this is not valid rust code\n";
    let (tool, _cleanup) =
        setup_script_tool("bad_rs", ScriptRuntime::Rust, script, Some(5000));
    let result = tool.execute(serde_json::json!({})).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().message.contains("rustc compile error"));
}

// [方法] Rust 二次执行使用缓存（mtime 对比跳过重编译）
#[tokio::test]
async fn execute_rust_cached_binary() {
    let (tool, _cleanup) = rust_echo_tool();
    // 第一次执行：编译 + 运行
    let r1 = tool
        .execute(serde_json::json!({"run": 1}))
        .await
        .unwrap();
    assert_eq!(r1["run"], 1);
    // 第二次执行：源文件未变 → 跳过编译，直接运行
    let r2 = tool
        .execute(serde_json::json!({"run": 2}))
        .await
        .unwrap();
    assert_eq!(r2["run"], 2);
}

// [边界] Rust 源文件不存在 → 返回 error
#[tokio::test]
async fn execute_rust_source_not_found() {
    let config = ToolConfig {
        name: "missing_rs".into(),
        description: "No such source".into(),
        runtime: ScriptRuntime::Rust,
        entrypoint: "ghost.rs".into(),
        timeout_ms: Some(5000),
        params_schema: serde_json::Value::Null,
    };
    let tool = ScriptTool::new(config, PathBuf::from("/tmp/nonexistent_rust_dir"));
    let result = tool.execute(serde_json::json!({})).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().message.contains("not found"));
}

// [方法] Rust stdin 大 JSON 编译执行
#[tokio::test]
async fn execute_rust_large_input() {
    let (tool, _cleanup) = rust_echo_tool();
    let large_data = serde_json::json!({"data": "x".repeat(10000)});
    let result = tool.execute(large_data.clone()).await.unwrap();
    assert_eq!(result["data"], large_data["data"]);
}

// ═══════════════════════════════════════════════════════════════
// ScriptTool 超时 — 2 tests
// ═══════════════════════════════════════════════════════════════

// [超时] 脚本执行超过 timeout_ms → timeout error
#[tokio::test]
async fn execute_timeout() {
    let script = "import sys, time\ntime.sleep(10)\nprint('{}')\n";
    let (tool, _cleanup) =
        setup_script_tool("sleeper", ScriptRuntime::Python, script, Some(100));
    let result = tool.execute(serde_json::json!({})).await;
    assert!(result.is_err());
    assert_eq!(result.unwrap_err().message, "timeout");
}

// [超时] 无超时（timeout_ms = None）时正常完成
#[tokio::test]
async fn execute_no_timeout_completes() {
    let (tool, _cleanup) = setup_script_tool(
        "quick",
        ScriptRuntime::Python,
        "import sys, json\nprint(json.dumps({'ok': True}))\n",
        None,
    );
    let result = tool.execute(serde_json::json!({})).await.unwrap();
    assert_eq!(result["ok"], true);
}

// ═══════════════════════════════════════════════════════════════
// ScriptTool cancel — 2 tests
// ═══════════════════════════════════════════════════════════════

// [取消] cancel 触发后 execute 返回 cancelled error
#[tokio::test]
async fn cancel_during_execution() {
    let script = "import sys, time\ntime.sleep(10)\nprint('{}')\n";
    let (tool, _cleanup) =
        setup_script_tool("long_runner", ScriptRuntime::Python, script, None);

    let tool_ref = std::sync::Arc::new(tool);
    let tool_clone = tool_ref.clone();

    let handle = tokio::spawn(async move { tool_clone.execute(serde_json::json!({})).await });

    tokio::time::sleep(std::time::Duration::from_millis(100)).await;

    tool_ref.cancel().await;

    let result = handle.await.unwrap();
    assert!(result.is_err());
    assert_eq!(result.unwrap_err().message, "cancelled");
}

// [取消] 对未在执行中的 tool 调用 cancel 不 panic
#[tokio::test]
async fn cancel_when_idle_no_panic() {
    let (tool, _cleanup) = python_echo_tool();
    tool.cancel().await;
}
