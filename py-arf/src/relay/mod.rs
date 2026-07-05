//! PyO3 bindings for ARF relay infrastructure — JsonlTailer + SseFormatter.
//!
//! Part of the team-engine feature (Phase 7 / V1.x task 6). Provides
//! SSE-formatted streaming output from JSONL trace files produced by
//! multiple engine instances.

pub mod jsonl_tailer;
pub mod sse_formatter;

pub use jsonl_tailer::PyJsonlTailer;
pub use sse_formatter::PySseFormatter;
