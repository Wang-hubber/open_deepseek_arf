//! Phase 6 E2E test helpers — shared by all integration test files.
//!
//! Each integration test file (e.g. `react_loop.rs`) declares `mod common;`
//! to import these helpers. Modules:
//! - [`env`] — env-var skip-if-missing pattern for live API keys
//! - [`provider`] — Provider factory: live (MiniMax/DeepSeek/OpenAI) and mock
//! - [`harness`] — E2EHarness unified setup (bus + nodes + engine)

pub mod env;
pub mod harness;
pub mod provider;
