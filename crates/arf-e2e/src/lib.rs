//! Phase 6 E2E test crate — see `tests/` for the actual test files and
//! `tests/common/` for shared helpers.
//!
//! This crate is published as `publish = false` — it is a workspace-local
//! test-only crate. It exists so that `cargo test -p arf-e2e` can run all
//! 15 E2E tests as a single unit, and so that the helpers (env, provider,
//! harness) can be shared across the four test files.
