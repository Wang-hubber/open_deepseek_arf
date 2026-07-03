//! Env-var helpers: skip-if-missing pattern for live API keys.
//!
//! Each helper returns `Option<String>`. Tests should call these and
//! short-circuit with `eprintln!` + `return` when `None`, producing a
//! clear "skipped" indicator rather than a hard failure.
//!
//! ## [构造] env-var reading and presence checks
//!
//! Note: tests that need a live provider should NOT be marked `#[ignore]`
//! at the attribute level — instead, they read the env var and return early
//! with a warning. This way, `cargo test -p arf-e2e` always succeeds (just
//! skips), and the same test becomes a live integration test when the key
//! is set in CI.

/// Read `MINIMAX_API_KEY` (or `MINIMAX_TOKEN` fallback). Returns `None` if
/// neither is set.
pub fn require_minimax_key() -> Option<String> {
    match std::env::var("MINIMAX_API_KEY").or_else(|_| std::env::var("MINIMAX_TOKEN")) {
        Ok(k) if !k.is_empty() => Some(k),
        _ => None,
    }
}

/// Read `DEEPSEEK_API_KEY`. Returns `None` if not set.
pub fn require_deepseek_key() -> Option<String> {
    match std::env::var("DEEPSEEK_API_KEY") {
        Ok(k) if !k.is_empty() => Some(k),
        _ => None,
    }
}

/// Read `OPENAI_API_KEY`. Returns `None` if not set.
pub fn require_openai_key() -> Option<String> {
    match std::env::var("OPENAI_API_KEY") {
        Ok(k) if !k.is_empty() => Some(k),
        _ => None,
    }
}

/// Read `DASHSCOPE_API_KEY`. Returns `None` if not set.
/// Used by Phase 9 task 9.2.2 live probe (阿里百炼 qwen).
pub fn require_dashscope_key() -> Option<String> {
    match std::env::var("DASHSCOPE_API_KEY") {
        Ok(k) if !k.is_empty() => Some(k),
        _ => None,
    }
}

/// Print the standard "[skip] KEY not set" line so test output is clear
/// about why a test did not run.
pub fn skip_message(key: &str) {
    eprintln!("[skip] {key} not set — corresponding E2E tests will skip");
}
