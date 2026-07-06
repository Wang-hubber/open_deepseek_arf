//! Backend errors with normalized codes for LLM consumption.

use thiserror::Error;

/// Errors returned by any `BackendProtocol` implementation.
///
/// Error variants that map to recoverable conditions (model can self-correct)
/// carry a `code: &'static str` matching DeepAgents' `FileOperationError`
/// literals (`file_not_found`, `permission_denied`, `is_directory`,
/// `invalid_path`). Backends MUST use these exact strings when raising
/// those conditions so that the LLM-tool error format is portable across
/// implementations.
#[derive(Debug, Error, serde::Serialize)]
#[serde(tag = "kind", content = "details")]
pub enum BackendError {
    /// Requested file does not exist.
    #[error("file not found: {path}")]
    FileNotFound { path: String, code: &'static str },

    /// Path points at a directory but a file was expected (or vice versa).
    #[error("path is a directory: {path}")]
    IsDirectory { path: String, code: &'static str },

    /// Path is malformed or contains invalid characters.
    #[error("invalid path: {path}")]
    InvalidPath { path: String, code: &'static str },

    /// Path exists but is not a directory (when directory was expected).
    #[error("not a directory: {path}")]
    NotADirectory { path: String },

    /// Permission denied by backend policy (sandboxing, ACL, etc.).
    #[error("permission denied: {path} — {reason}")]
    PermissionDenied { path: String, reason: String, code: &'static str },

    /// Edit failed because `old_string` matched zero or multiple occurrences.
    #[error("edit match error: {reason}")]
    EditMatch { reason: String },

    /// Backend-specific IO error (encode/decode, network, etc.).
    #[error("io error: {0}")]
    Io(String),

    /// Operation exceeded backend-defined timeout.
    #[error("operation timed out after {timeout_ms}ms")]
    Timeout { timeout_ms: u64 },

    /// Backend does not implement the requested operation (e.g. `delete`
    /// on a read-only StateBackend slice).
    #[error("unsupported operation: {0}")]
    Unsupported(&'static str),

    /// Catch-all for backend-internal errors that don't fit above.
    #[error("backend error: {0}")]
    Other(String),
}

impl BackendError {
    /// Return the normalized error code for LLM consumption, or `None`
    /// if the variant is backend-specific.
    pub fn code(&self) -> Option<&'static str> {
        match self {
            Self::FileNotFound { code, .. } => Some(code),
            Self::IsDirectory { code, .. } => Some(code),
            Self::InvalidPath { code, .. } => Some(code),
            Self::PermissionDenied { code, .. } => Some(code),
            Self::EditMatch { .. } => Some("edit_match"),
            Self::Timeout { .. } => Some("timeout"),
            _ => None,
        }
    }
}