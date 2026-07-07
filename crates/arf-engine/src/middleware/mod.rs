//! Middleware implementations for the ARF Engine.

pub mod filesystem;
pub mod summarization;

pub use filesystem::{
    parse_backend_path, FilesystemMiddleware, FS_TOOL_EDIT, FS_TOOL_GLOB, FS_TOOL_GREP,
    FS_TOOL_LS, FS_TOOL_READ, FS_TOOL_WRITE,
};
pub use summarization::{
    SummarizationMiddleware, DEFAULT_KEEP_TAIL, DEFAULT_TRIGGER_RATIO,
};