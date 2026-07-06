//! `CompositeBackend` — path-prefix routing backend (Phase 11 / 11.4).
//!
//! Dispatches each operation to a child backend based on `BackendPath`'s
//! longest-matching prefix. Unmatched paths fall through to a default
//! backend. Allows applications to compose state + filesystem + skills
//! backends without changing middleware code.

use std::sync::Arc;

use async_trait::async_trait;

use crate::error::BackendError;
use crate::path::BackendPath;
use crate::protocol::BackendProtocol;
use crate::types::{
    DeleteResult, EditResult, FileInfo, GlobResult, GrepMatch, GrepResult, LsResult,
    ReadResult, WriteResult,
};

/// Composite backend with path-prefix routing.
///
/// Construct via [`CompositeBackend::new`] and add routes via
/// [`CompositeBackend::with_route`]. Routes are evaluated by **longest
/// prefix first**, so order of insertion does not matter.
pub struct CompositeBackend {
    /// (prefix, child backend) pairs, kept sorted by prefix length (longest first).
    routes: Vec<(BackendPath, Arc<dyn BackendProtocol>)>,
    /// Default backend for paths that don't match any route.
    default: Arc<dyn BackendProtocol>,
}

impl CompositeBackend {
    /// Create a new `CompositeBackend` with the given default backend.
    pub fn new(default: Arc<dyn BackendProtocol>) -> Self {
        Self {
            routes: Vec::new(),
            default,
        }
    }

    /// Add a route: paths starting with `prefix` are dispatched to
    /// `child`. Returns `self` for chaining.
    pub fn with_route(
        mut self,
        prefix: BackendPath,
        child: Arc<dyn BackendProtocol>,
    ) -> Self {
        self.routes.push((prefix, child));
        self.routes
            .sort_by_key(|(p, _)| std::cmp::Reverse(p.as_str().len()));
        self
    }

    /// Resolve a `BackendPath` to `(Arc backend, translated_path)` using
    /// the longest-matching prefix.
    pub fn resolve(&self, path: &BackendPath) -> (Arc<dyn BackendProtocol>, BackendPath) {
        for (prefix, child) in &self.routes {
            if prefix.is_ancestor_of(path) {
                let suffix = path
                    .strip_prefix(prefix)
                    .unwrap_or_else(|| path.as_str().to_string());
                let translated = if suffix.is_empty() {
                    prefix.clone()
                } else {
                    BackendPath::new(&format!("/{suffix}"))
                        .unwrap_or_else(|_| path.clone())
                };
                return (child.clone(), translated);
            }
        }
        (self.default.clone(), path.clone())
    }

    /// Find the child `Arc<dyn BackendProtocol>` whose prefix matches
    /// the given absolute path. Used by `upload_files` to dispatch a
    /// bucketed group back to its backend.
    fn arc_for_path(&self, path: &BackendPath) -> Arc<dyn BackendProtocol> {
        for (prefix, child) in &self.routes {
            if prefix.is_ancestor_of(path) {
                return child.clone();
            }
        }
        self.default.clone()
    }
}

#[async_trait]
impl BackendProtocol for CompositeBackend {
    async fn ls(&self, path: &BackendPath) -> Result<LsResult, BackendError> {
        let (child, translated) = self.resolve(path);
        child.ls(&translated).await
    }

    async fn read(
        &self,
        path: &BackendPath,
        offset: u32,
        limit: u32,
    ) -> Result<ReadResult, BackendError> {
        let (child, translated) = self.resolve(path);
        child.read(&translated, offset, limit).await
    }

    async fn write(&self, path: &BackendPath, content: &str) -> Result<WriteResult, BackendError> {
        let (child, translated) = self.resolve(path);
        child.write(&translated, content).await
    }

    async fn edit(
        &self,
        path: &BackendPath,
        old_string: &str,
        new_string: &str,
        replace_all: bool,
    ) -> Result<EditResult, BackendError> {
        let (child, translated) = self.resolve(path);
        child
            .edit(&translated, old_string, new_string, replace_all)
            .await
    }

    async fn delete(&self, path: &BackendPath) -> Result<DeleteResult, BackendError> {
        let (child, translated) = self.resolve(path);
        child.delete(&translated).await
    }

    async fn glob(
        &self,
        pattern: &str,
        base: Option<&BackendPath>,
    ) -> Result<GlobResult, BackendError> {
        if let Some(base) = base {
            let (child, translated) = self.resolve(base);
            return child.glob(pattern, Some(&translated)).await;
        }
        let mut merged: Vec<FileInfo> = Vec::new();
        let mut truncated = false;
        let default_result = self.default.glob(pattern, None).await?;
        if let Some(entries) = default_result.matches {
            merged.extend(entries);
        }
        truncated |= default_result.truncated;
        // For each route, dispatch with `None` base — child backends
        // operate in their own coordinate system (e.g., StateBackend
        // stores files at translated paths without the route prefix).
        // We re-prefix on merge to restore absolute paths.
        for (prefix, child) in &self.routes {
            let result = child.glob(pattern, None).await?;
            if let Some(entries) = result.matches {
                for entry in entries {
                    let new_path = rebuild_composite_path(prefix.as_str(), &entry.path);
                    merged.push(FileInfo {
                        path: new_path,
                        ..entry
                    });
                }
            }
            truncated |= result.truncated;
        }
        Ok(GlobResult {
            error: None,
            matches: Some(merged),
            truncated,
        })
    }

    async fn grep(
        &self,
        pattern: &str,
        path: Option<&BackendPath>,
        glob_filter: Option<&str>,
    ) -> Result<GrepResult, BackendError> {
        if let Some(p) = path {
            let (child, translated) = self.resolve(p);
            return child.grep(pattern, Some(&translated), glob_filter).await;
        }
        let mut merged: Vec<GrepMatch> = Vec::new();
        let mut truncated = false;
        let default_result = self.default.grep(pattern, None, glob_filter).await?;
        if let Some(matches) = default_result.matches {
            merged.extend(matches);
        }
        truncated |= default_result.truncated;
        // For each route, dispatch with `None` path — child backends
        // operate in their own coordinate system; we re-prefix on merge.
        for (prefix, child) in &self.routes {
            let result = child.grep(pattern, None, glob_filter).await?;
            if let Some(matches) = result.matches {
                for m in matches {
                    let new_path = rebuild_composite_path(prefix.as_str(), &m.path);
                    merged.push(GrepMatch {
                        path: new_path,
                        line: m.line,
                        text: m.text,
                    });
                }
            }
            truncated |= result.truncated;
        }
        Ok(GrepResult {
            error: None,
            matches: Some(merged),
            truncated,
        })
    }

    async fn upload_files(
        &self,
        files: Vec<(BackendPath, Vec<u8>)>,
    ) -> Result<Vec<Result<(), BackendError>>, BackendError> {
        // Bucket files by Arc identity. Two files routed to the same
        // child backend land in the same bucket; we then dispatch each
        // bucket to its Arc in one batch call.
        let mut buckets: std::collections::HashMap<BucketKey, Vec<(BackendPath, Vec<u8>)>> =
            std::collections::HashMap::new();
        let mut order: Vec<BucketKey> = Vec::new();
        let mut file_to_bucket: Vec<BucketKey> = Vec::with_capacity(files.len());
        for (path, bytes) in files {
            let (child, translated) = self.resolve(&path);
            let key = BucketKey::new(&child);
            file_to_bucket.push(key.clone());
            if !buckets.contains_key(&key) {
                order.push(key);
            }
            buckets
                .entry(BucketKey::new(&child))
                .or_default()
                .push((translated, bytes));
        }

        // Dispatch each bucket to its child backend by matching Arc identity.
        let all_backends: Vec<&Arc<dyn BackendProtocol>> = self
            .routes
            .iter()
            .map(|(_, c)| c)
            .chain(std::iter::once(&self.default))
            .collect();

        let mut group_results: Vec<Vec<Result<(), BackendError>>> = Vec::new();
        for key in &order {
            if let Some(group) = buckets.remove(key) {
                let mut found: Option<Arc<dyn BackendProtocol>> = None;
                for backend in &all_backends {
                    if BucketKey::new(backend) == *key {
                        found = Some((*backend).clone());
                        break;
                    }
                }
                let child = found.unwrap_or_else(|| self.default.clone());
                let r = child.upload_files(group).await?;
                group_results.push(r);
            }
        }
        Ok(group_results.into_iter().flatten().collect())
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Identity-key for bucketing files by their target backend.
///
/// Uses the data pointer of the `Arc<dyn BackendProtocol>` allocation
/// (the trait-object fat pointer's first word). Two `Arc`s pointing at
/// the same heap allocation compare equal here.
#[derive(Clone, Eq, PartialEq, Hash)]
struct BucketKey(usize);

impl BucketKey {
    fn new(arc: &Arc<dyn BackendProtocol>) -> Self {
        // Cast trait-object fat pointer to thin first, then to usize.
        let thin: *const () = Arc::as_ptr(arc) as *const ();
        Self(thin as usize)
    }
}

/// Re-build a composite-absolute path from a child-relative path.
fn rebuild_composite_path(prefix: &str, child_path: &str) -> String {
    if child_path == "/" || child_path.is_empty() {
        prefix.to_string()
    } else {
        let trimmed_prefix = prefix.trim_end_matches('/');
        format!("{trimmed_prefix}{child_path}")
    }
}