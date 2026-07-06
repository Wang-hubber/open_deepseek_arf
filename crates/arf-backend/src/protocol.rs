//! `BackendProtocol` — the unified async trait for file/memory/sandbox backends.
//!
//! This is the core abstraction of Phase 11 / G-01. All concrete backends
//! (StateBackend, FilesystemBackend, CompositeBackend, …) implement this
//! trait, and all middleware (FilesystemMiddleware, MemoryMiddleware,
//! SkillsMiddleware, …) consume it via `Arc<dyn BackendProtocol>`.

use async_trait::async_trait;

use crate::error::BackendError;
use crate::path::BackendPath;
use crate::types::{
    DeleteResult, EditResult, ExecuteResponse, GlobResult, GrepResult, LsResult, ReadResult,
    WriteResult,
};

/// Pluggable backend for file/memory/sandbox operations.
///
/// All methods are async — ARFV1 is async-first (engine, session, mcp are
/// all async). Sync backends MUST wrap their work in `tokio::task::spawn_blocking`
/// rather than blocking the runtime.
///
/// **Path sandboxing**: every method receives a `&BackendPath` (normalized,
/// `..`-free, absolute). Implementations MAY enforce additional restrictions
/// (real-FS roots, ACLs) but MUST accept any well-formed `BackendPath`.
#[async_trait]
pub trait BackendProtocol: Send + Sync {
    /// List all entries in a directory (non-recursive).
    ///
    /// Returns `LsResult` with `error = Some(...)` on failure (path missing,
    /// permission denied, etc.). The error string is suitable for direct
    /// forwarding to the LLM.
    async fn ls(&self, path: &BackendPath) -> Result<LsResult, BackendError>;

    /// Read a single file with optional line range.
    ///
    /// `offset` is 0-indexed line number to start from; `limit` is the
    /// maximum number of lines to return. Implementations MAY truncate
    /// lines longer than 2000 chars (DeepAgents convention).
    async fn read(
        &self,
        path: &BackendPath,
        offset: u32,
        limit: u32,
    ) -> Result<ReadResult, BackendError>;

    /// Write content to a file, creating it or overwriting it.
    async fn write(&self, path: &BackendPath, content: &str) -> Result<WriteResult, BackendError>;

    /// Exact-string replacement in an existing file.
    ///
    /// If `replace_all` is `false` (default), `old_string` MUST be unique in
    /// the file; otherwise `EditMatch` error is returned.
    async fn edit(
        &self,
        path: &BackendPath,
        old_string: &str,
        new_string: &str,
        replace_all: bool,
    ) -> Result<EditResult, BackendError>;

    /// Recursively delete a path (file or directory).
    ///
    /// `delete` is **optional** — backends that do not implement it inherit
    /// this default, which returns `BackendError::Unsupported`. Callers that
    /// need to support a mix of backends should check via
    /// `BackendProtocol::supports_delete(&backend)` (helper to be added in
    /// 11.5) before calling, or catch `BackendError::Unsupported`.
    async fn delete(&self, path: &BackendPath) -> Result<DeleteResult, BackendError> {
        let _ = path;
        Err(BackendError::Unsupported("delete"))
    }

    /// Find files matching a glob pattern.
    ///
    /// `pattern` supports standard glob wildcards (`*`, `**`, `?`, `[abc]`).
    /// `base` is an optional base directory; if `None`, the backend uses its
    /// default search root.
    async fn glob(
        &self,
        pattern: &str,
        base: Option<&BackendPath>,
    ) -> Result<GlobResult, BackendError>;

    /// Search for a literal text pattern in files.
    ///
    /// `pattern` is a literal substring (NOT regex).
    /// `path` is the optional directory to search; `glob_filter` is an
    /// optional filename glob to narrow which files are searched.
    async fn grep(
        &self,
        pattern: &str,
        path: Option<&BackendPath>,
        glob_filter: Option<&str>,
    ) -> Result<GrepResult, BackendError>;

    /// Batch upload of files. Used by sandbox backends for bulk ingest.
    ///
    /// Returns one result per input, in the same order. Successful uploads
    /// have `error = None`; failures carry a backend-specific error string.
    ///
    /// Default impl raises `Unsupported`; backends that don't support
    /// batch upload (e.g. read-only backends) inherit this.
    async fn upload_files(
        &self,
        files: Vec<(BackendPath, Vec<u8>)>,
    ) -> Result<Vec<Result<(), BackendError>>, BackendError> {
        let _ = files;
        Err(BackendError::Unsupported("upload_files"))
    }
}

/// Extension trait for sandbox backends that can execute shell commands.
///
/// `BackendProtocol` covers file operations; `SandboxBackendProtocol` adds
/// shell execution + sandbox identity. Implemented by sandbox backends
/// (LocalShellBackend, E2BSandboxAdapter, LangSmithSandboxAdapter — all in
/// Phase 11-M4 / G-04). File-only backends (StateBackend, FilesystemBackend
/// without execute support) do NOT implement this trait.
#[async_trait]
pub trait SandboxBackendProtocol: BackendProtocol {
    /// Unique identifier for this sandbox instance (e.g. `"e2b-<container-id>"`).
    fn id(&self) -> &str;

    /// Execute a shell command in the sandbox.
    ///
    /// `timeout_ms` is the maximum wall-clock time before the command is
    /// killed. Implementations should honour this with `tokio::time::timeout`
    /// or equivalent. A `None` value disables the timeout (NOT recommended
    /// for untrusted input).
    async fn execute(
        &self,
        command: &str,
        timeout_ms: Option<u64>,
    ) -> Result<ExecuteResponse, BackendError>;
}