//! ARF Pluggable Backend (Phase 11 / G-01).
//!
//! Provides a uniform async trait for pluggable file/memory/sandbox backends.
//! Implementations: StateBackend (11.2), FilesystemBackend (11.3),
//! CompositeBackend (11.4). SandboxBackendProtocol extension in Phase 11-M4.
//!
//! This crate defines the trait and types only — no implementations live here.

#![doc(test(attr(deny(warnings))))]

pub mod composite;
pub mod error;
pub mod factory;
pub mod filesystem;
pub mod local_shell;
pub mod path;
pub mod protocol;
pub mod state;
pub mod types;

pub use composite::CompositeBackend;
pub use error::BackendError;
pub use factory::{resolve_backend, BackendFactory, BackendFactoryFn, BackendSpec, IntoBackend};
pub use filesystem::FilesystemBackend;
pub use local_shell::LocalShellBackend;
pub use path::BackendPath;
pub use protocol::{BackendProtocol, SandboxBackendProtocol};
pub use state::{StateBackend, DEFAULT_STATE_BACKEND_ID};
pub use types::{
    DeleteResult, EditResult, ExecuteResponse, FileData, FileInfo, GlobResult, GrepMatch,
    GrepResult, LsResult, ReadResult, WriteResult,
};