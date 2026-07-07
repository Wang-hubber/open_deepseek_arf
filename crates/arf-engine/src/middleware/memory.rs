//! `MemoryMiddleware` — load AGENTS.md and persistent memory into system prompt.
//!
//! Reads `AGENTS.md` (or configured path) from a backend at agent startup
//! and injects the contents into the system prompt. The backend can be any
//! `BackendProtocol` — typically `FilesystemBackend` rooted at the project
//! directory, or `StateBackend` for testing.

use std::sync::Arc;

use arf_backend::{BackendError, BackendPath, BackendProtocol};
use arf_core::{Middleware, ModelRequest, State};
use async_trait::async_trait;

/// Default path for AGENTS.md inside the backend.
pub const DEFAULT_MEMORY_PATH: &str = "/AGENTS.md";

/// Header prepended to the loaded AGENTS.md content in the system prompt.
pub const MEMORY_HEADER: &str = "\n\n# AGENTS.md (Persistent Memory)\n\n";

/// Memory middleware — loads AGENTS.md from backend.
pub struct MemoryMiddleware {
    backend: Arc<dyn BackendProtocol>,
    /// Backend path to the memory file.
    memory_path: BackendPath,
    /// Header to prepend to loaded content.
    header: String,
    /// If true, log a warning when memory file is missing (default: false).
    warn_if_missing: bool,
}

impl MemoryMiddleware {
    /// Create with default memory path (`/AGENTS.md`).
    pub fn new(backend: Arc<dyn BackendProtocol>) -> Self {
        Self {
            backend,
            memory_path: BackendPath::new(DEFAULT_MEMORY_PATH)
                .expect("static path is valid"),
            header: MEMORY_HEADER.to_string(),
            warn_if_missing: false,
        }
    }

    /// Set a custom memory path.
    pub fn with_memory_path(mut self, path: BackendPath) -> Self {
        self.memory_path = path;
        self
    }

    /// Set a custom header (e.g., to identify the source).
    pub fn with_header(mut self, header: impl Into<String>) -> Self {
        self.header = header.into();
        self
    }

    /// Enable warnings on missing memory file.
    pub fn warn_on_missing(mut self) -> Self {
        self.warn_if_missing = true;
        self
    }

    /// Borrow the wrapped backend.
    pub fn backend(&self) -> &Arc<dyn BackendProtocol> {
        &self.backend
    }

    /// Read the memory file from the backend. Returns `Ok(None)` if the
    /// file doesn't exist (per design choice for v1).
    pub async fn load_memory(&self) -> Result<Option<String>, BackendError> {
        let result = self.backend.read(&self.memory_path, 0, 0).await?;
        if result.error.is_some() {
            return Ok(None);
        }
        let data = result.file_data;
        Ok(data.map(|d| d.content))
    }
}

#[async_trait]
impl Middleware for MemoryMiddleware {
    fn name(&self) -> &str {
        "memory"
    }

    async fn before_agent(&self, _state: &State) {
        match self.load_memory().await {
            Ok(Some(content)) => {
                tracing::info!(
                    path = %self.memory_path,
                    bytes = content.len(),
                    "MemoryMiddleware: loaded AGENTS.md"
                );
            }
            Ok(None) => {
                if self.warn_if_missing {
                    tracing::warn!(
                        path = %self.memory_path,
                        "MemoryMiddleware: no AGENTS.md found at configured path"
                    );
                }
            }
            Err(e) => {
                tracing::error!(
                    path = %self.memory_path,
                    error = %e,
                    "MemoryMiddleware: failed to load AGENTS.md"
                );
            }
        }
    }

    async fn before_model_call(&self, ctx: &mut ModelRequest, _state: &State) {
        if let Ok(Some(content)) = self.load_memory().await {
            ctx.extend_system_suffix(format!("{}{}", self.header, content));
        }
    }

    async fn after_agent(&self, _state: &State, _final_output: &str) {
        // v1: no-op. v2 will persist memory writes via this hook.
    }
}