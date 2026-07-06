//! Result and metadata types used across backend operations.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Information about a single file or directory entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileInfo {
    /// Absolute backend path.
    pub path: String,
    /// Whether this entry is a directory.
    #[serde(default)]
    pub is_dir: bool,
    /// File size in bytes (0 for directories).
    #[serde(default)]
    pub size: u64,
    /// Last modification timestamp (UTC).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub modified_at: Option<DateTime<Utc>>,
}

/// Stored file data with content and metadata.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileData {
    /// File content as UTF-8 text or base64-encoded binary.
    pub content: String,
    /// Encoding marker: `"utf-8"` or `"base64"`.
    pub encoding: String,
    /// Creation timestamp (UTC).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub created_at: Option<DateTime<Utc>>,
    /// Modification timestamp (UTC).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub modified_at: Option<DateTime<Utc>>,
}

/// Result of a `BackendProtocol::read` call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReadResult {
    /// Error message on failure; `None` on success.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub error: Option<String>,
    /// File data on success; `None` on failure.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub file_data: Option<FileData>,
}

/// Result of a `BackendProtocol::write` call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WriteResult {
    /// Error message on failure; `None` on success.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub error: Option<String>,
    /// Absolute path of the written file; `None` on failure.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub path: Option<String>,
}

/// Result of a `BackendProtocol::edit` call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EditResult {
    /// Error message on failure; `None` on success.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub error: Option<String>,
    /// Absolute path of the edited file; `None` on failure.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub path: Option<String>,
    /// Number of replacements actually performed (0 on failure).
    #[serde(default)]
    pub occurrences: u32,
}

/// Result of a `BackendProtocol::delete` call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeleteResult {
    /// Error message on failure; `None` on success.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub error: Option<String>,
    /// Absolute path that was deleted; `None` on failure.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub path: Option<String>,
}

/// Result of a `BackendProtocol::ls` call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LsResult {
    /// Error message on failure; `None` on success.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub error: Option<String>,
    /// Directory entries on success; `None` on failure.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub entries: Option<Vec<FileInfo>>,
}

/// A single match from `BackendProtocol::grep`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GrepMatch {
    /// Path to the file containing the match.
    pub path: String,
    /// 1-indexed line number of the match.
    pub line: u32,
    /// Content of the matching line (without trailing newline).
    pub text: String,
}

/// Result of a `BackendProtocol::grep` call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GrepResult {
    /// Error message on failure; `None` on success.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub error: Option<String>,
    /// Matches on success; may also be populated when the search was
    /// truncated (caller must check `truncated`). `None` only on hard failure.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub matches: Option<Vec<GrepMatch>>,
    /// True if the search stopped early (timeout, cap).
    #[serde(default)]
    pub truncated: bool,
}

/// Result of a `BackendProtocol::glob` call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GlobResult {
    /// Error message on failure; `None` on success.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub error: Option<String>,
    /// Matching file entries on success; `None` on hard failure.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub matches: Option<Vec<FileInfo>>,
    /// True if the walk stopped early (timeout, cap).
    #[serde(default)]
    pub truncated: bool,
}

/// Result of a `BackendProtocol::execute` call (sandbox backends only).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecuteResponse {
    /// Combined stdout + stderr output of the executed command.
    pub output: String,
    /// Process exit code (`None` if the process was killed by a signal).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub exit_code: Option<i32>,
    /// True if the output was truncated by backend size limits.
    #[serde(default)]
    pub truncated: bool,
}