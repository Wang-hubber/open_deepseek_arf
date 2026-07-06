//! `BackendFactory` — env-driven backend construction (Phase 11 / 11.5).
//!
//! Single entry point for apps that need a `BackendProtocol`. Resolves
//! `ARF_BACKEND=...` env var to a concrete backend instance.

use std::sync::Arc;

use crate::error::BackendError;
use crate::filesystem::FilesystemBackend;
use crate::protocol::BackendProtocol;
use crate::state::StateBackend;

/// Result of parsing an `ARF_BACKEND` env value.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BackendSpec {
    /// In-memory StateBackend (no config).
    State,
    /// FilesystemBackend rooted at the given path.
    Filesystem(String),
    /// Unknown backend type — surfaces as error at construction.
    Unknown(String),
}

impl BackendSpec {
    /// Parse an `ARF_BACKEND` value string into a `BackendSpec`.
    ///
    /// Format: `<type>[:<config>]`
    /// - `state` — StateBackend
    /// - `filesystem:<path>` — FilesystemBackend rooted at `<path>`
    pub fn parse(raw: &str) -> Self {
        let (ty, cfg) = match raw.split_once(':') {
            Some((t, c)) => (t, c),
            None => (raw, ""),
        };
        match ty {
            "state" => Self::State,
            "filesystem" => Self::Filesystem(cfg.to_string()),
            other => Self::Unknown(other.to_string()),
        }
    }
}

/// Backend factory: env-driven construction + default fallback.
pub struct BackendFactory;

impl BackendFactory {
    /// Read the `ARF_BACKEND` env var and build a backend.
    ///
    /// - Unset or empty → `BackendSpec::State` → StateBackend
    /// - `state` → StateBackend
    /// - `filesystem:<path>` → FilesystemBackend rooted at `<path>`
    /// - Unknown → `Err(BackendError::Other)`
    pub fn from_env() -> Result<Arc<dyn BackendProtocol>, BackendError> {
        let raw = std::env::var("ARF_BACKEND").unwrap_or_default();
        let spec = if raw.is_empty() {
            BackendSpec::State
        } else {
            BackendSpec::parse(&raw)
        };
        Self::from_spec(spec)
    }

    /// Build a backend from a parsed spec.
    pub fn from_spec(spec: BackendSpec) -> Result<Arc<dyn BackendProtocol>, BackendError> {
        match spec {
            BackendSpec::State => Ok(Arc::new(StateBackend::new())),
            BackendSpec::Filesystem(root) => {
                let backend = FilesystemBackend::new(&root)?;
                Ok(Arc::new(backend))
            }
            BackendSpec::Unknown(ty) => Err(BackendError::Other(format!(
                "unknown backend type: {ty}"
            ))),
        }
    }

    /// Return the safe default backend (StateBackend).
    pub fn default_backend() -> Arc<dyn BackendProtocol> {
        Arc::new(StateBackend::new())
    }
}

/// Trait for "anything that can become a backend" — either an instance
/// or a factory closure.
pub trait IntoBackend {
    fn into_backend(self) -> Arc<dyn BackendProtocol>;
}

impl IntoBackend for Arc<dyn BackendProtocol> {
    fn into_backend(self) -> Arc<dyn BackendProtocol> {
        self
    }
}

impl IntoBackend for Box<dyn BackendProtocol> {
    fn into_backend(self) -> Arc<dyn BackendProtocol> {
        Arc::from(self)
    }
}

impl IntoBackend for StateBackend {
    fn into_backend(self) -> Arc<dyn BackendProtocol> {
        Arc::new(self)
    }
}

impl IntoBackend for FilesystemBackend {
    fn into_backend(self) -> Arc<dyn BackendProtocol> {
        Arc::new(self)
    }
}

/// Factory closure: `Arc<dyn BackendProtocol>` produced on demand.
pub type BackendFactoryFn = Box<dyn Fn() -> Arc<dyn BackendProtocol> + Send + Sync>;

impl IntoBackend for BackendFactoryFn {
    fn into_backend(self) -> Arc<dyn BackendProtocol> {
        (self)()
    }
}

/// Resolve a backend from either an instance or a factory callable.
pub fn resolve_backend<B>(backend: B) -> Arc<dyn BackendProtocol>
where
    B: IntoBackend,
{
    backend.into_backend()
}