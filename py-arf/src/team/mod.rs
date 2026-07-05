//! PyO3 bindings for the ARF Team abstraction.
//!
//! Phase 7 / V1.x task 8. The `team` module exposes three layers:
//!
//!   - `TeamConfig` — pure-data description of a team's structure
//!     (parsed from `team.yaml` or built programmatically). This is
//!     the contract that the engine builder and downstream code
//!     consume.
//!   - `TeamBuilder` — constructor that takes a `TeamConfig` plus an
//!     optional `Bus` and produces a `Team`.
//!   - `Team` — runtime container that owns the long-lived persistent
//!     engines and the subagent pools. The current skeleton wires the
//!     configuration through but does not yet spawn actual engines or
//!     pools — those are follow-up tasks.
//!
//! The implementation deliberately mirrors the per-relay-module style
//! (Task 6 / Task 7): types live in their own file under `team/`, the
//! module re-exports the public surface, and `lib.rs` registers each
//! pyclass individually.

pub mod team_builder;
pub mod team_config;

pub use team_builder::{PyTeam, PyTeamBuilder};
pub use team_config::{PyEngineSpec, PyPoolSpec, PyTeamConfig};