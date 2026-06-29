# 任务 5.2：ScriptTool + tool.toml 解析

> Phase 5 — MCP 第二项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.1 (类型定义)

## 设计思路

`ScriptTool` 是框架中唯一的本地 Tool 实现——实现 `Tool` trait，包裹外部脚本，通过 **stdin/stdout JSON 协议** 执行。`ToolConfig::from_toml_str()` 将 `tool.toml` 文件内容解析为结构化配置。

**核心设计决策**：

- **所有本地 Tool 都是 ScriptTool**——不区分"内置"和"脚本"，`DiscoveryModule`（Task 5.6）扫描 `{root}/tools/*/tool.toml` 统一发现
- **取消机制**：`ScriptTool` 持有 `Mutex<Option<oneshot::Sender>>`，`execute()` 内部创建 oneshot channel，`cancel()` 触发 sender → `execute()` 的 `tokio::select!` 收到信号 → `child.start_kill()` 终止子进程
- **超时**：通过 `tokio::select!` 的 `timeout` 分支实现，超时后 `start_kill()` 终止子进程
- **Rust runtime**：entrypoint 指向 `.rs` 源文件，首次执行 `rustc` 编译 → 二进制缓存到 tool 目录，后续通过 mtime 对比判断是否需要重编译。编译错误作为 `ToolError` 返回

| 文件 | 操作 | 内容 |
|------|------|------|
| `Cargo.toml` | 更新 | 添加 `toml`、`tokio`（process/time/sync）依赖 |
| `config.rs` | 更新 | `ToolConfig::from_toml_str()` |
| `script.rs` | 新建 | `ScriptTool` struct + `Tool` trait 实现 |
| `lib.rs` | 更新 | `pub mod script;` |

---

## 代码实现

### `crates/arf-mcp/Cargo.toml` 更新

在 `[dependencies]` 中添加 `toml` 和 `tokio`：

```toml
[dependencies]
arf-core = { path = "../arf-core" }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
async-trait = "0.1"
toml = "0.8"
tokio = { version = "1", features = ["process", "time", "sync"] }

[dev-dependencies]
tokio = { version = "1", features = ["rt", "macros"] }
```

逐行解释：
- `toml = "0.8"` — TOML 反序列化，`ToolConfig::from_toml_str()` 用
- `tokio` features `process` — `tokio::process::Command` 异步子进程管理
- `tokio` features `time` — `tokio::time::sleep` 超时控制
- `tokio` features `sync` — `tokio::sync::oneshot` 取消信号
- `[dev-dependencies]` tokio 仅需 `rt` + `macros`（`#[tokio::test]`），`process/time/sync` 从主依赖继承

---

### `crates/arf-mcp/src/config.rs` 更新

在现有 `ToolConfig` 的 `impl` 块中添加：

```rust
impl ToolConfig {
    /// Parse a `tool.toml` file content into a `ToolConfig`.
    ///
    /// The TOML format uses lowercase runtime names ("python", "bash", "rust")
    /// matching `ScriptRuntime`'s `#[serde(rename_all = "lowercase")]`.
    ///
    /// `params_schema` can be specified as inline TOML table syntax:
    /// ```toml
    /// [params_schema]
    /// type = "object"
    /// properties.path.type = "string"
    /// ```
    /// The TOML table deserializes into `serde_json::Value::Object`.
    pub fn from_toml_str(content: &str) -> Result<Self, String> {
        toml::from_str(content).map_err(|e| format!("invalid tool.toml: {e}"))
    }
}
```

逐行解释：
- `toml::from_str(content)` — TOML crate 通过 serde 直接将 TOML 字符串反序列化为 `ToolConfig`
- `params_schema: serde_json::Value` — `serde_json::Value` 实现了通用的 `Deserialize`，TOML table 会反序列化为 `Value::Object`，TOML array 会反序列化为 `Value::Array`
- 错误映射为 `String` — `DiscoveryModule`（Task 5.6）负责错误聚合

---

### `crates/arf-mcp/src/script.rs` — 新建

```rust
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use async_trait::async_trait;
use serde_json::Value;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;
use tokio::sync::oneshot;

use crate::config::{ScriptRuntime, ToolConfig};
use crate::tool::Tool;
use crate::types::ToolError;

/// A Tool implementation that wraps an external script.
///
/// ScriptTool implements the Tool trait, so it works with the standard
/// DAG executor — no special path. The `execute()` method:
/// 1. (Rust only) Compile .rs → cached binary via mtime comparison
/// 2. Starts a child process (python3 / bash / compiled Rust binary)
/// 3. Writes params as JSON to stdin
/// 4. Reads result as JSON from stdout
/// 5. Captures stderr for error reporting
/// 6. Kills the process on timeout or cancel
pub struct ScriptTool {
    /// Tool name from tool.toml.
    name: String,
    /// Tool description from tool.toml.
    description: String,
    /// Which runtime to use.
    runtime: ScriptRuntime,
    /// Path to the tool directory (contains entrypoint script).
    tool_dir: PathBuf,
    /// Entry point filename (e.g. "main.sh").
    entrypoint: String,
    /// Per-call timeout. None = no timeout.
    timeout_ms: Option<u64>,
    /// JSON Schema for parameters.
    params_schema: Value,
    /// Cancellation sender — set during execute(), cleared on completion.
    /// `cancel()` takes the sender and fires it, causing `execute()` to
    /// abort via `tokio::select!`.
    cancel_tx: Mutex<Option<oneshot::Sender<()>>>,
}

impl ScriptTool {
    /// Create a ScriptTool from a parsed ToolConfig and its directory.
    ///
    /// The `tool_dir` is the directory containing `tool.toml` and the
    /// entrypoint script. `entrypoint` is relative to `tool_dir`.
    pub fn new(config: ToolConfig, tool_dir: PathBuf) -> Self {
        Self {
            name: config.name,
            description: config.description,
            runtime: config.runtime,
            tool_dir,
            entrypoint: config.entrypoint,
            timeout_ms: config.timeout_ms,
            params_schema: config.params_schema,
            cancel_tx: Mutex::new(None),
        }
    }

    /// Build the platform command for this tool's runtime.
    ///
    /// For Rust, this also handles on-demand compilation:
    /// - First call: `rustc source -o binary` → cache binary next to source
    /// - Subsequent calls: compare mtime, recompile only if source changed
    /// - Compilation errors are returned as `ToolError`
    async fn build_command(&self) -> Result<Command, ToolError> {
        let entrypoint_path = self.tool_dir.join(&self.entrypoint);
        match self.runtime {
            ScriptRuntime::Python => {
                let mut cmd = Command::new("python3");
                cmd.arg(&entrypoint_path);
                Ok(cmd)
            }
            ScriptRuntime::Bash => {
                let mut cmd = Command::new("bash");
                cmd.arg(&entrypoint_path);
                Ok(cmd)
            }
            ScriptRuntime::Rust => {
                // entrypoint 是 .rs 源文件，binary 去掉 .rs 后缀
                let source = &entrypoint_path;
                let binary = self.tool_dir.join(
                    self.entrypoint.trim_end_matches(".rs")
                );

                // mtime 对比：源文件是否比已有二进制更新
                let needs_compile = match (fs::metadata(source), fs::metadata(&binary)) {
                    (Ok(src_meta), Ok(bin_meta)) => {
                        let src_time = src_meta.modified()
                            .map_err(|e| ToolError::from(format!("stat src: {e}")))?;
                        let bin_time = bin_meta.modified()
                            .map_err(|e| ToolError::from(format!("stat bin: {e}")))?;
                        src_time > bin_time
                    }
                    (Ok(_), Err(_)) => true, // 二进制不存在 → 编译
                    (Err(_), _) => {
                        return Err(ToolError::from(format!(
                            "source file not found: {}",
                            source.display()
                        )));
                    }
                };

                if needs_compile {
                    let output = Command::new("rustc")
                        .arg(source)
                        .arg("-o").arg(&binary)
                        .arg("-C").arg("opt-level=2")
                        .output()
                        .await
                        .map_err(|e| ToolError::from(format!("rustc spawn: {e}")))?;

                    if !output.status.success() {
                        let stderr = String::from_utf8_lossy(&output.stderr);
                        return Err(ToolError::from(format!(
                            "rustc compile error:\n{}",
                            stderr
                        )));
                    }
                }

                Ok(Command::new(&binary))
            }
        }
    }
}

#[async_trait]
impl Tool for ScriptTool {
    fn name(&self) -> &str {
        &self.name
    }

    fn description(&self) -> &str {
        &self.description
    }

    fn parameters_schema(&self) -> Value {
        self.params_schema.clone()
    }

    async fn execute(&self, params: Value) -> Result<Value, ToolError> {
        // 1. Build command (Rust: compile if needed)
        let mut child = self
            .build_command()
            .await?
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| ToolError::from(format!("failed to spawn process: {e}")))?;

        // 2. Write params JSON to stdin, then close the pipe
        {
            let mut stdin = child
                .stdin
                .take()
                .ok_or_else(|| ToolError::from("failed to open stdin"))?;
            let params_json = serde_json::to_string(&params)
                .map_err(|e| ToolError::from(format!("json encode error: {e}")))?;
            stdin
                .write_all(params_json.as_bytes())
                .await
                .map_err(|e| ToolError::from(format!("write stdin error: {e}")))?;
            stdin
                .shutdown()
                .await
                .map_err(|e| ToolError::from(format!("close stdin error: {e}")))?;
            // stdin dropped here — pipe closed
        }

        // 3. Setup cancellation channel
        let (cancel_tx, mut cancel_rx) = oneshot::channel();
        *self.cancel_tx.lock().unwrap() = Some(cancel_tx);

        // 4. Wait for child with optional timeout and cancellation
        let wait_fut = child.wait_with_output();
        tokio::pin!(wait_fut);

        let output = if let Some(ms) = self.timeout_ms {
            let timeout = tokio::time::sleep(std::time::Duration::from_millis(ms));
            tokio::pin!(timeout);

            tokio::select! {
                result = &mut wait_fut => {
                    // Normal completion
                    result
                }
                _ = &mut cancel_rx => {
                    // Cancelled — child killed by kill_on_drop
                    child.start_kill().ok();
                    return Err(ToolError::from("cancelled"));
                }
                _ = &mut timeout => {
                    // Timeout — kill child
                    child.start_kill().ok();
                    return Err(ToolError::from("timeout"));
                }
            }
        } else {
            tokio::select! {
                result = &mut wait_fut => {
                    result
                }
                _ = &mut cancel_rx => {
                    child.start_kill().ok();
                    return Err(ToolError::from("cancelled"));
                }
            }
        };

        // 5. Clear cancellation sender
        *self.cancel_tx.lock().unwrap() = None;

        let output = output.map_err(|e| ToolError::from(format!("process error: {e}")))?;

        // 6. Check exit status
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            let code = output.status.code().unwrap_or(-1);
            return Err(ToolError::from(format!(
                "exit code {code}: {}",
                stderr.trim()
            )));
        }

        // 7. Parse stdout as JSON
        let stdout = String::from_utf8_lossy(&output.stdout);
        serde_json::from_str(&stdout)
            .map_err(|e| ToolError::from(format!("invalid JSON from script: {e}")))
    }

    async fn cancel(&self) {
        if let Some(tx) = self.cancel_tx.lock().unwrap().take() {
            let _ = tx.send(());
        }
    }
}
```

逐行解释：
- `kill_on_drop(true)` — 确保 `ScriptTool` 被 drop 时子进程被 kill（防止僵尸进程）
- `stdin.take()` — 获取子进程 stdin 写入端的所有权，写完 JSON 后 drop 关闭管道，脚本读到 EOF
- `oneshot::channel()` — 一次性信号通道。`execute()` 持有 `receiver`，`cancel()` 持有 `sender`。`cancel()` 调用 `tx.send(())` → `execute()` 的 `tokio::select!` 收到信号 → `start_kill()` + 返回 `"cancelled"`
- `tokio::pin!` — 对 `wait_fut` 和 `timeout` 做堆 pin，使其可用于 `tokio::select!` 的 `&mut` 分支引用
- `select!` 分支语义：正常完成走 `wait_fut`，cancel 和 timeout 走各自分支并主动 `start_kill()`
- `start_kill()` — 发送 SIGKILL（Unix）或 TerminateProcess（Windows），不等子进程退出直接返回
- `Mutex<Option<oneshot::Sender>>` — `execute(&self)` 和 `cancel(&self)` 都借 `&self`，需要 `Mutex` 做内部可变性；`Option` 表示"是否正在执行"

**stdin/stdout JSON 协议**：脚本从 stdin 读 JSON params，往 stdout 写 JSON result。stderr 用于错误诊断——脚本 exit code 非 0 时 stderr 作为 error message 返回。

---

### `crates/arf-mcp/src/lib.rs` 更新

```rust
pub mod config;
pub mod script;
pub mod tool;
pub mod types;

#[cfg(test)]
mod tests;
```

---

## 测试

### 测试结构

```
crates/arf-mcp/src/tests/
├── mod.rs               # 添加 script_tests
├── config_tests.rs       # 追加 TOML 解析测试
├── script_tests.rs       # 新建
├── tool_tests.rs
└── types_tests.rs
```

### `crates/arf-mcp/src/tests/mod.rs` 更新

```rust
mod config_tests;
mod script_tests;
mod tool_tests;
mod types_tests;
```

---

### `crates/arf-mcp/src/tests/config_tests.rs` 追加 TOML 解析测试

在现有测试末尾追加：

```rust
// ═══════════════════════════════════════════════════════════════
// ToolConfig::from_toml_str — 8 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 完整 tool.toml 解析成功
#[test]
fn tool_config_from_toml_full() {
    let toml_content = r#"
name = "cleanup_logs"
description = "Delete log files older than N days"
runtime = "bash"
entrypoint = "main.sh"
timeout_ms = 30000

[params_schema]
type = "object"
"#;
    let config = ToolConfig::from_toml_str(toml_content).unwrap();
    assert_eq!(config.name, "cleanup_logs");
    assert_eq!(config.description, "Delete log files older than N days");
    assert_eq!(config.runtime, ScriptRuntime::Bash);
    assert_eq!(config.entrypoint, "main.sh");
    assert_eq!(config.timeout_ms, Some(30000));
    assert_eq!(config.params_schema["type"], "object");
}

// [构造] 最小 tool.toml（仅必填字段）
#[test]
fn tool_config_from_toml_minimal() {
    let toml_content = r#"
name = "hello"
description = "Say hello"
runtime = "python"
entrypoint = "hello.py"
"#;
    let config = ToolConfig::from_toml_str(toml_content).unwrap();
    assert_eq!(config.name, "hello");
    assert_eq!(config.runtime, ScriptRuntime::Python);
    assert_eq!(config.timeout_ms, None);
    assert_eq!(config.params_schema, serde_json::Value::Null);
}

// [构造] Python runtime 解析
#[test]
fn tool_config_from_toml_runtime_python() {
    let config = ToolConfig::from_toml_str(
        r#"name="t" description="d" runtime="python" entrypoint="e.py""#,
    )
    .unwrap();
    assert_eq!(config.runtime, ScriptRuntime::Python);
}

// [构造] Bash runtime 解析
#[test]
fn tool_config_from_toml_runtime_bash() {
    let config = ToolConfig::from_toml_str(
        r#"name="t" description="d" runtime="bash" entrypoint="e.sh""#,
    )
    .unwrap();
    assert_eq!(config.runtime, ScriptRuntime::Bash);
}

// [构造] Rust runtime 解析
#[test]
fn tool_config_from_toml_runtime_rust() {
    let config = ToolConfig::from_toml_str(
        r#"name="t" description="d" runtime="rust" entrypoint="t""#,
    )
    .unwrap();
    assert_eq!(config.runtime, ScriptRuntime::Rust);
}

// [边界] 无效 runtime 字符串 → 报错
#[test]
fn tool_config_from_toml_invalid_runtime() {
    let result = ToolConfig::from_toml_str(
        r#"name="t" description="d" runtime="javascript" entrypoint="x""#,
    );
    assert!(result.is_err());
}

// [边界] 缺少必填字段 name → 报错
#[test]
fn tool_config_from_toml_missing_name() {
    let result = ToolConfig::from_toml_str(
        r#"description="d" runtime="python" entrypoint="x""#,
    );
    assert!(result.is_err());
}

// [边界] params_schema 为嵌套 TOML table
#[test]
fn tool_config_from_toml_nested_params_schema() {
    let toml_content = r#"
name = "search"
description = "Full-text search"
runtime = "python"
entrypoint = "search.py"

[params_schema]
type = "object"

[params_schema.properties.query]
type = "string"
description = "Search query"

[params_schema.properties.max_results]
type = "integer"
default = 10
"#;
    let config = ToolConfig::from_toml_str(toml_content).unwrap();
    let schema = &config.params_schema;
    assert_eq!(schema["type"], "object");
    assert_eq!(schema["properties"]["query"]["type"], "string");
    assert_eq!(schema["properties"]["max_results"]["type"], "integer");
    assert_eq!(schema["properties"]["max_results"]["default"], 10);
}
```

---

### `crates/arf-mcp/src/tests/script_tests.rs` — 新建

测试策略：
- 使用临时目录 + 写入真实脚本文件来测试 `ScriptTool.execute()`
- Python、Bash、Rust 三语言均用真实子进程验证
- Rust 测试验证编译 + 执行 + 缓存重编译跳过三条路径
- Rust 测试要求 `rustc` 在 PATH 中（开发环境默认满足）
- 每个测试创建独立 temp dir，测试结束后自动清理

```rust
use std::fs;
use std::path::PathBuf;

use crate::config::{ScriptRuntime, ToolConfig};
use crate::script::ScriptTool;
use crate::tool::Tool;
use serde_json::Value;

/// Create a temp directory, write a script file into it, return (tool_dir, config).
fn setup_script_tool(
    name: &str,
    runtime: ScriptRuntime,
    script_content: &str,
    timeout_ms: Option<u64>,
) -> (ScriptTool, PathBuf) {
    let temp = std::env::temp_dir().join(format!("arf_mcp_test_{name}"));
    let _ = fs::remove_dir_all(&temp); // clean up previous run
    fs::create_dir_all(&temp).unwrap();

    let (entrypoint, _script_path) = match runtime {
        ScriptRuntime::Python => {
            let p = temp.join("main.py");
            fs::write(&p, script_content).unwrap();
            ("main.py".to_string(), p)
        }
        ScriptRuntime::Bash => {
            let p = temp.join("main.sh");
            fs::write(&p, script_content).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&p, fs::Permissions::from_mode(0o755)).unwrap();
            }
            ("main.sh".to_string(), p)
        }
        ScriptRuntime::Rust => {
            let entry = "main.rs";
            let p = temp.join(entry);
            fs::write(&p, script_content).unwrap();
            (entry.to_string(), p)
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

// Helper so that temp dirs get cleaned up.
// ScriptTool tests return (ScriptTool, PathBuf) — wrap the PathBuf in Cleanup.
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
        Some(5000),
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
    let (tool, _cleanup) = setup_script_tool(
        "failer",
        ScriptRuntime::Python,
        script,
        Some(5000),
    );
    let result = tool.execute(serde_json::json!({})).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().message.contains("exit code"));
}

// [方法] 脚本输出非 JSON → 返回 parse error
#[tokio::test]
async fn execute_script_invalid_json_output() {
    let script = "print('not json')\n";
    let (tool, _cleanup) = setup_script_tool(
        "badjson",
        ScriptRuntime::Python,
        script,
        Some(5000),
    );
    let result = tool.execute(serde_json::json!({})).await;
    assert!(result.is_err());
}

// [方法] stderr 写入不影响成功结果（只检查 exit code 和 stdout）
#[tokio::test]
async fn execute_script_with_stderr_output() {
    let script = "import sys, json\nparams = json.loads(sys.stdin.read())\nsys.stderr.write('warning: deprecated')\nprint(json.dumps(params))\n";
    let (tool, _cleanup) = setup_script_tool(
        "stderr_warn",
        ScriptRuntime::Python,
        script,
        Some(5000),
    );
    let result = tool
        .execute(serde_json::json!({"ok": true}))
        .await
        .unwrap();
    // stderr does not affect result parsing — stdout is the only source
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
    let (tool, _cleanup) = setup_script_tool(
        "bad_rs",
        ScriptRuntime::Rust,
        script,
        Some(5000),
    );
    let result = tool.execute(serde_json::json!({})).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().message.contains("rustc compile error"));
}

// [方法] Rust 二次执行使用缓存（mtime 对比跳过重编译）
#[tokio::test]
async fn execute_rust_cached_binary() {
    let (tool, _cleanup) = rust_echo_tool();
    // 第一次执行：编译 + 运行
    let r1 = tool.execute(serde_json::json!({"run": 1})).await.unwrap();
    assert_eq!(r1["run"], 1);
    // 第二次执行：源文件未变 → 跳过编译，直接运行
    let r2 = tool.execute(serde_json::json!({"run": 2})).await.unwrap();
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
    let (tool, _cleanup) = setup_script_tool(
        "sleeper",
        ScriptRuntime::Python,
        script,
        Some(100), // 100ms timeout
    );
    let result = tool.execute(serde_json::json!({})).await;
    assert!(result.is_err());
    assert_eq!(result.unwrap_err().message, "timeout");
}

// [超时] 无超时（timeout_ms = None）时长时间运行不报错
#[tokio::test]
async fn execute_no_timeout_completes() {
    let (tool, _cleanup) = setup_script_tool(
        "quick",
        ScriptRuntime::Python,
        "import sys, json\nprint(json.dumps({'ok': True}))\n",
        None, // no timeout
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
    let (tool, _cleanup) = setup_script_tool(
        "long_runner",
        ScriptRuntime::Python,
        script,
        None, // no timeout, cancelled explicitly
    );

    let tool_ref = std::sync::Arc::new(tool);
    let tool_clone = tool_ref.clone();

    // Spawn execute in background
    let handle = tokio::spawn(async move { tool_clone.execute(serde_json::json!({})).await });

    // Give it a moment to start
    tokio::time::sleep(std::time::Duration::from_millis(100)).await;

    // Cancel the execution
    tool_ref.cancel().await;

    // Execute should return cancelled
    let result = handle.await.unwrap();
    assert!(result.is_err());
    assert_eq!(result.unwrap_err().message, "cancelled");
}

// [取消] 对未在执行中的 tool 调用 cancel 不 panic
#[tokio::test]
async fn cancel_when_idle_no_panic() {
    let (tool, _cleanup) = python_echo_tool();
    tool.cancel().await; // no execute in progress — should not panic
}
```

---

## 验证命令

```bash
# 编译检查
. "$HOME/.cargo/env" && cargo check -p arf-mcp

# 运行 arf-mcp 测试
. "$HOME/.cargo/env" && cargo test -p arf-mcp

# Workspace 全量测试
. "$HOME/.cargo/env" && cargo test --workspace
```

---

## 测试覆盖摘要

| 文件 | 新增测试 | 覆盖角度 |
|------|---------|---------|
| `config_tests.rs` | 8 | `[构造][边界]` — TOML 解析（full/minimal/all runtimes/invalid runtime/missing field/nested schema） |
| `script_tests.rs` | 20 | `[构造][方法][边界][超时][取消]` — ScriptTool 构造(3)、execute(13 含 5 Rust)、超时(2)、取消(2) |
| **合计** | **28** | 累计 arf-mcp: 69 + 28 = **97 tests** |

---

## 实施记录

> 以下是代码转录过程中遇到的问题和调整，如实记录。

### 1. tokio feature 不足

**问题**：`tokio = { features = ["process", "time", "sync"] }` 缺少 `io-util` 和 `macros`。

- `AsyncWriteExt::write_all()` / `shutdown()` 需要 `io-util` feature
- `tokio::select!` 宏需要 `macros` feature

**修复**：Cargo.toml 中 tokio features 改为：

```toml
tokio = { version = "1", features = ["process", "time", "sync", "io-util", "macros"] }
```

### 2. Child 所有权竞争

**问题**：`child.wait_with_output()` 消耗 `child` 的所有权（`fn wait_with_output(mut self)`），导致 cancel/timeout 分支无法再调用 `child.start_kill()`。

**文档中的写法**（有问题）：
```rust
let wait_fut = child.wait_with_output();  // child moved
tokio::pin!(wait_fut);
tokio::select! {
    result = &mut wait_fut => { result }
    _ = &mut cancel_rx => {
        child.start_kill().ok();  // ❌ borrow after move
        ...
    }
}
```

**修复**：用 `Arc<Mutex<Option<Child>>>` 共享所有权——`wait_fut` 和 cancel/timeout 分支谁先触发谁取走 child：

```rust
let child_cell = Arc::new(Mutex::new(Some(child)));
let wait_fut = {
    let cell = child_cell.clone();
    async move {
        let child = cell.lock().unwrap().take().unwrap();
        child.wait_with_output().await
    }
};
tokio::pin!(wait_fut);
tokio::select! {
    result = &mut wait_fut => { result }
    _ = &mut cancel_rx => {
        if let Some(mut c) = child_cell.lock().unwrap().take() {
            c.start_kill().ok();  // ✅ take 成功，wait_fut 不会再拿到 child
        }
        return Err(ToolError::from("cancelled"));
    }
}
```

原理：`Mutex` 保护 `Option<Child>`，`take()` 取走所有权。`kill_on_drop(true)` 确保被 `wait_fut` 拿走的 child 在 drop 时也会被清理。

### 3. TOML 内联格式不兼容

**问题**：测试中使用了 `name="t" description="d" runtime="python" entrypoint="e.py"` 这种 JSON 风格的单行写法。

TOML 规范不支持逗号分隔的 key-value 对在同一行——它要求换行或 table 语法。

**修复**：将单行 TOML 改为标准多行格式：

```toml
# ❌ 不合法
name="t" description="d" runtime="python" entrypoint="e.py"

# ✅ 合法
name = "t"
description = "d"
runtime = "python"
entrypoint = "e.py"
```

### 4. 测试并行竞争（temp 目录冲突）

**问题**：多个 Rust 测试（`execute_rust_echo`、`execute_rust_cached_binary`、`execute_rust_large_input`）共享同一个 temp 目录 `/tmp/arf_mcp_test_echo_rs`。`cargo test` 默认并行运行测试，导致目录被并发删除/重建，出现 "No such file or directory"。

**修复**：给每个测试调用分配唯一的 temp 目录，使用原子计数器：

```rust
static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

fn setup_script_tool(...) -> (ScriptTool, PathBuf) {
    let id = TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let temp = std::env::temp_dir().join(format!("arf_mcp_test_{name}_{id}"));
    ...
}
```

### 5. 未使用的 import 清理

`script_tests.rs` 中 `use serde_json::Value;` 并未直接使用（测试使用 `serde_json::json!` 宏和完整路径 `serde_json::Value::Null`）。已移除该 import。
