//! `BackendPath` — a sandbox-safe path representation.
//!
//! All backend methods take `&BackendPath` (not `&str`) to make path
//! sandboxing and normalization explicit at the type level.

use std::path::{Component, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::BackendError;

/// A normalized, sandbox-checked path string.
///
/// Invariants:
/// - Always starts with `/` (absolute, never relative)
/// - Never contains `.` or `..` components
/// - Never contains NUL bytes
/// - Never contains backslashes (POSIX-only paths)
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct BackendPath(String);

impl BackendPath {
    /// Create a new `BackendPath` from a raw string, normalizing and validating.
    ///
    /// Normalization:
    /// - Strip leading `./` and `/./` segments
    /// - Reject `..` components (no escape via relative traversal)
    /// - Reject NUL bytes and backslashes
    ///
    /// Returns `Err(BackendError::InvalidPath)` if the path violates any invariant.
    pub fn new(raw: &str) -> Result<Self, BackendError> {
        if raw.is_empty() {
            return Err(BackendError::InvalidPath {
                path: raw.into(),
                code: "invalid_path",
            });
        }
        if raw.contains('\0') {
            return Err(BackendError::InvalidPath {
                path: raw.into(),
                code: "invalid_path",
            });
        }
        if raw.contains('\\') {
            return Err(BackendError::InvalidPath {
                path: raw.into(),
                code: "invalid_path",
            });
        }

        // Split on `/`, filter empty + `.` + `..` segments.
        let mut normalized = PathBuf::new();
        for component in raw.split('/') {
            match component {
                "" | "." => continue,
                ".." => {
                    return Err(BackendError::InvalidPath {
                        path: raw.into(),
                        code: "invalid_path",
                    });
                }
                c => normalized.push(c),
            }
        }

        // Reassemble with leading `/`.
        let mut out = String::from("/");
        out.push_str(
            normalized
                .components()
                .filter_map(|c| match c {
                    Component::Normal(s) => Some(s.to_string_lossy().into_owned()),
                    _ => None,
                })
                .collect::<Vec<_>>()
                .join("/")
                .as_str(),
        );
        Ok(Self(out))
    }

    /// Borrow the underlying path as a string slice.
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Check if this path is an ancestor of (or equal to) `other`.
    /// Used by CompositeBackend for prefix routing.
    pub fn is_ancestor_of(&self, other: &BackendPath) -> bool {
        if self == other {
            return true;
        }
        other.0.starts_with(&self.0)
            && (self.0.ends_with('/') || other.0[self.0.len()..].starts_with('/'))
    }

    /// Return the path suffix relative to `prefix`. Caller must ensure
    /// `self.is_ancestor_of(prefix)` is true first.
    pub fn strip_prefix(&self, prefix: &BackendPath) -> Option<String> {
        let stripped = self.0.strip_prefix(prefix.as_str())?;
        let trimmed = stripped.trim_start_matches('/');
        Some(trimmed.to_string())
    }
}

impl std::fmt::Display for BackendPath {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl AsRef<str> for BackendPath {
    fn as_ref(&self) -> &str {
        &self.0
    }
}