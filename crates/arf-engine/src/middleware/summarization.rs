//! `SummarizationMiddleware` — automatic context-window summarization.
//!
//! In `before_model_call`, checks if `state.context_tokens / model_context_window`
//! exceeds a configurable threshold; if so, logs an advisory message.
//!
//! **Note**: This middleware is advisory in v1 — the actual compaction is
//! performed by `CheckpointRule::when_context_over` (already wired into the
//! engine). A future v2 may extend the `Middleware` trait to accept `&mut State`
//! for direct mutation.

use std::sync::Arc;

use arf_compactor::Compactor;
use arf_core::{Middleware, ModelRequest, State};
use async_trait::async_trait;

/// Default threshold: trigger compaction at 80% of model context window.
pub const DEFAULT_TRIGGER_RATIO: f64 = 0.80;

/// Default keep-tail (most recent N messages to preserve verbatim).
pub const DEFAULT_KEEP_TAIL: usize = 6;

/// Summarization middleware — auto-compacts when context overflows.
pub struct SummarizationMiddleware {
    #[allow(dead_code)]
    compactor: Arc<Compactor>,
    /// Trigger when context_tokens / model_context_window >= ratio.
    trigger_ratio: f64,
    /// Number of most-recent messages to keep verbatim.
    #[allow(dead_code)]
    keep_tail: usize,
}

impl SummarizationMiddleware {
    pub fn new(compactor: Arc<Compactor>) -> Self {
        Self {
            compactor,
            trigger_ratio: DEFAULT_TRIGGER_RATIO,
            keep_tail: DEFAULT_KEEP_TAIL,
        }
    }

    pub fn with_trigger_ratio(mut self, ratio: f64) -> Self {
        self.trigger_ratio = ratio.clamp(0.0, 1.0);
        self
    }

    pub fn with_keep_tail(mut self, n: usize) -> Self {
        self.keep_tail = n;
        self
    }

    /// Check if state context exceeds trigger ratio.
    pub fn should_compact(&self, state: &State) -> bool {
        let window = state.over_view.model_context_window;
        if window == 0 {
            return false;
        }
        let usage = state.over_view.context_tokens as f64 / window as f64;
        usage >= self.trigger_ratio
    }

    /// Current trigger ratio (for inspection).
    pub fn trigger_ratio(&self) -> f64 {
        self.trigger_ratio
    }

    /// Current keep_tail value (for inspection).
    pub fn keep_tail(&self) -> usize {
        self.keep_tail
    }
}

#[async_trait]
impl Middleware for SummarizationMiddleware {
    fn name(&self) -> &str {
        "summarization"
    }

    async fn before_model_call(&self, _ctx: &mut ModelRequest, state: &State) {
        if !self.should_compact(state) {
            return;
        }
        tracing::info!(
            context_tokens = state.over_view.context_tokens,
            window = state.over_view.model_context_window,
            ratio = self.trigger_ratio,
            "SummarizationMiddleware: context overflow detected; Engine should trigger compaction"
        );
    }
}