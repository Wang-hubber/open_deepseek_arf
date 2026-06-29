use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

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
    cancel_tx: Mutex<Option<oneshot::Sender<()>>>,
}

impl ScriptTool {
    /// Create a ScriptTool from a parsed ToolConfig and its directory.
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
                let source = &entrypoint_path;
                let binary = self
                    .tool_dir
                    .join(self.entrypoint.trim_end_matches(".rs"));

                let needs_compile = match (fs::metadata(source), fs::metadata(&binary)) {
                    (Ok(src_meta), Ok(bin_meta)) => {
                        let src_time = src_meta
                            .modified()
                            .map_err(|e| ToolError::from(format!("stat src: {e}")))?;
                        let bin_time = bin_meta
                            .modified()
                            .map_err(|e| ToolError::from(format!("stat bin: {e}")))?;
                        src_time > bin_time
                    }
                    (Ok(_), Err(_)) => true,
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
                        .arg("-o")
                        .arg(&binary)
                        .arg("-C")
                        .arg("opt-level=2")
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

#[async_trait::async_trait]
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
        }

        // 3. Setup cancellation channel
        let (cancel_tx, mut cancel_rx) = oneshot::channel();
        *self.cancel_tx.lock().unwrap() = Some(cancel_tx);

        // 4. Wait for child with optional timeout and cancellation.
        //    child is inside Arc<Mutex<Option>> so both the wait_fut
        //    (takes ownership via wait_with_output) and cancel/timeout
        //    (needs child.start_kill()) can access it — whichever wins.
        let child_cell = Arc::new(Mutex::new(Some(child)));
        let wait_fut = {
            let cell = child_cell.clone();
            async move {
                let child = cell.lock().unwrap().take().unwrap();
                child.wait_with_output().await
            }
        };
        tokio::pin!(wait_fut);

        let output = if let Some(ms) = self.timeout_ms {
            let timeout = tokio::time::sleep(std::time::Duration::from_millis(ms));
            tokio::pin!(timeout);

            tokio::select! {
                result = &mut wait_fut => {
                    result
                }
                _ = &mut cancel_rx => {
                    if let Some(mut c) = child_cell.lock().unwrap().take() {
                        c.start_kill().ok();
                    }
                    return Err(ToolError::from("cancelled"));
                }
                _ = &mut timeout => {
                    if let Some(mut c) = child_cell.lock().unwrap().take() {
                        c.start_kill().ok();
                    }
                    return Err(ToolError::from("timeout"));
                }
            }
        } else {
            tokio::select! {
                result = &mut wait_fut => {
                    result
                }
                _ = &mut cancel_rx => {
                    if let Some(mut c) = child_cell.lock().unwrap().take() {
                        c.start_kill().ok();
                    }
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
