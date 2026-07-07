//! `LocalShellBackend` — shell execute + filesystem ops, sandboxed to a CWD.
//!
//! Implements both `BackendProtocol` and `SandboxBackendProtocol`. File
//! operations are confined to `cwd`; shell commands execute with `cwd` as
//! the working directory. **Opt-in only** — apps must explicitly choose
//! this backend (default is `StateBackend` per Phase 10 G-04).

use std::path::{Path, PathBuf};
use std::process::Stdio;

use async_trait::async_trait;
use tokio::process::Command;

use crate::error::BackendError;
use crate::filesystem::FilesystemBackend;
use crate::path::BackendPath;
use crate::protocol::{BackendProtocol, SandboxBackendProtocol};
use crate::types::{
    DeleteResult, EditResult, ExecuteResponse, GlobResult, GrepResult, LsResult, ReadResult,
    WriteResult,
};

/// Local shell backend — files + exec, all sandboxed to a root directory.
pub struct LocalShellBackend {
    /// Filesystem operations delegate here (for path sandboxing).
    fs: FilesystemBackend,
    /// CWD for shell commands.
    cwd: PathBuf,
    /// Default timeout for execute() (millis).
    default_timeout_ms: u64,
    /// Output capture buffer size cap (bytes).
    output_cap_bytes: usize,
}

impl LocalShellBackend {
    /// Create with a sandbox root directory.
    pub fn new(cwd: impl Into<PathBuf>) -> Result<Self, BackendError> {
        let cwd = cwd.into();
        if !cwd.is_absolute() {
            return Err(BackendError::InvalidPath {
                path: cwd.to_string_lossy().into(),
                code: "invalid_path",
            });
        }
        if !cwd.exists() {
            return Err(BackendError::Io(format!(
                "cwd does not exist: {}",
                cwd.display()
            )));
        }
        let fs = FilesystemBackend::new(cwd.clone())?;
        Ok(Self {
            fs,
            cwd,
            default_timeout_ms: 30_000,
            output_cap_bytes: 1024 * 1024, // 1MB
        })
    }

    /// Set default timeout for execute() (millis).
    pub fn with_default_timeout(mut self, ms: u64) -> Self {
        self.default_timeout_ms = ms;
        self
    }

    /// Set output capture cap (bytes). Output beyond this is truncated.
    pub fn with_output_cap(mut self, bytes: usize) -> Self {
        self.output_cap_bytes = bytes;
        self
    }

    /// Return the sandbox root.
    pub fn cwd(&self) -> &Path {
        &self.cwd
    }
}

#[async_trait]
impl BackendProtocol for LocalShellBackend {
    async fn ls(&self, path: &BackendPath) -> Result<LsResult, BackendError> {
        self.fs.ls(path).await
    }

    async fn read(
        &self,
        path: &BackendPath,
        offset: u32,
        limit: u32,
    ) -> Result<ReadResult, BackendError> {
        self.fs.read(path, offset, limit).await
    }

    async fn write(&self, path: &BackendPath, content: &str) -> Result<WriteResult, BackendError> {
        self.fs.write(path, content).await
    }

    async fn edit(
        &self,
        path: &BackendPath,
        old_string: &str,
        new_string: &str,
        replace_all: bool,
    ) -> Result<EditResult, BackendError> {
        self.fs.edit(path, old_string, new_string, replace_all).await
    }

    async fn delete(&self, path: &BackendPath) -> Result<DeleteResult, BackendError> {
        self.fs.delete(path).await
    }

    async fn glob(
        &self,
        pattern: &str,
        base: Option<&BackendPath>,
    ) -> Result<GlobResult, BackendError> {
        self.fs.glob(pattern, base).await
    }

    async fn grep(
        &self,
        pattern: &str,
        path: Option<&BackendPath>,
        glob_filter: Option<&str>,
    ) -> Result<GrepResult, BackendError> {
        self.fs.grep(pattern, path, glob_filter).await
    }

    async fn upload_files(
        &self,
        files: Vec<(BackendPath, Vec<u8>)>,
    ) -> Result<Vec<Result<(), BackendError>>, BackendError> {
        self.fs.upload_files(files).await
    }
}

#[async_trait]
impl SandboxBackendProtocol for LocalShellBackend {
    fn id(&self) -> &str {
        "local-shell"
    }

    async fn execute(
        &self,
        command: &str,
        timeout_ms: Option<u64>,
    ) -> Result<ExecuteResponse, BackendError> {
        let timeout = timeout_ms.unwrap_or(self.default_timeout_ms);
        let mut cmd = Command::new("sh");
        cmd.arg("-c").arg(command);
        cmd.current_dir(&self.cwd);
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());

        let output = match tokio::time::timeout(
            std::time::Duration::from_millis(timeout),
            cmd.output(),
        )
        .await
        {
            Ok(Ok(out)) => out,
            Ok(Err(e)) => return Err(BackendError::Io(format!("spawn failed: {e}"))),
            Err(_) => {
                return Err(BackendError::Timeout {
                    timeout_ms: timeout,
                });
            }
        };

        let mut combined = format!(
            "STDOUT:\n{}\nSTDERR:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let truncated = combined.len() > self.output_cap_bytes;
        if truncated {
            combined.truncate(self.output_cap_bytes);
        }

        Ok(ExecuteResponse {
            output: combined,
            exit_code: output.status.code(),
            truncated,
        })
    }
}