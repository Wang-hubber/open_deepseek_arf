//! ARF Pluggable Backend (Phase 11 / G-01).
//!
//! Provides a uniform async trait for pluggable file/memory/sandbox backends.
//! Implementations: StateBackend (11.2), FilesystemBackend (11.3),
//! CompositeBackend (11.4). SandboxBackendProtocol extension in Phase 11-M4.
//!
//! This crate defines the trait and types only — no implementations live here.

#![doc(test(attr(deny(warnings))))]

pub mod error;
pub mod path;
pub mod protocol;
pub mod state;
pub mod types;

pub use error::BackendError;
pub use path::BackendPath;
pub use protocol::{BackendProtocol, SandboxBackendProtocol};
pub use state::{StateBackend, DEFAULT_STATE_BACKEND_ID};
pub use types::{
    DeleteResult, EditResult, ExecuteResponse, FileData, FileInfo, GlobResult, GrepMatch,
    GrepResult, LsResult, ReadResult, WriteResult,
};