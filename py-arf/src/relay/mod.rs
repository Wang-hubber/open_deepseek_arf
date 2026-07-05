//! PyO3 bindings for ARF relay infrastructure.
//!
//! Part of the team-engine feature (Phase 7 / V1.x tasks 6+7).
//! Provides SSE-formatted streaming output from JSONL trace files
//! produced by multiple engine instances:
//!
//!   - `JsonlTailer`   — async iterator over one JSONL trace file
//!   - `SseFormatter`  — `id: / event: / data:` triple helpers
//!   - `TeamMembership`— union of YAML-defined engine IDs (and,
//!                       eventually, live Bus nodes)
//!   - `EventFilter`   — predicate over `(engine_id, msg_type)`
//!   - `SseRelay`      — high-level aggregator that ties the above
//!                       together and yields an async stream of SSE
//!                       strings

pub mod event_filter;
pub mod jsonl_tailer;
pub mod sse_formatter;
pub mod sse_relay;
pub mod sse_relay_stream;
pub mod team_membership;

pub use event_filter::PyEventFilter;
pub use jsonl_tailer::PyJsonlTailer;
pub use sse_formatter::PySseFormatter;
pub use sse_relay::PySseRelay;
pub use sse_relay_stream::PySseRelayStream;
pub use team_membership::PyTeamMembership;