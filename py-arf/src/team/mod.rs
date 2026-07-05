//! PyO3 bindings for the ARF Team abstraction.
//!
//! Phase 7 / V1.x task 8 (skeleton) → task 14 (real wiring). The `team`
//! module exposes:
//!
//!   - `TeamConfig` — pure-data description of a team's structure
//!     (parsed from `team.yaml` or built programmatically).
//!   - `EngineSpec` / `PoolSpec` — entries on a `TeamConfig`'s roster.
//!   - `TeamBuilder` — constructor that takes a `TeamConfig` plus a
//!     `Bus` and produces a `Team`.
//!   - `Team` — runtime container that owns long-lived persistent
//!     `Engine`s and `SubagentPool`s. `Team.engine(id)` and
//!     `Team.subagent_pool(id)` return Python handles usable from
//!     request handlers.
//!   - `EngineHandle` / `PoolHandle` — Python-side wrappers around
//!     the underlying Rust types (engine.rs / team_builder.rs).
//!
//! Implementation style mirrors the per-relay-module convention (Task 6
//! / Task 7): types live in their own file under `team/`, the module
//! re-exports the public surface, and `lib.rs` registers each pyclass
//! individually.

pub mod team_builder;
pub mod team_config;

pub use team_builder::{PyPoolHandle, PyTeam, PyTeamBuilder};
pub use team_config::{PyEngineSpec, PyPoolSpec, PyTeamConfig};