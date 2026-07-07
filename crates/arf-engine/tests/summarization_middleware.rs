//! Tests for `SummarizationMiddleware` (Phase 11 / 11.8).

use std::sync::Arc;

use arf_compactor::Compactor;
use arf_core::{Middleware, ModelMessage, ModelRequest, OverView, State};
use arf_engine::middleware::{SummarizationMiddleware, DEFAULT_KEEP_TAIL, DEFAULT_TRIGGER_RATIO};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_compactor() -> Arc<Compactor> {
    use arf_compactor::Summarizer;
    struct StubSummarizer;
    #[async_trait::async_trait]
    impl Summarizer for StubSummarizer {
        async fn summarize(
            &self,
            req: arf_compactor::CompactionRequest<'_>,
        ) -> Result<String, arf_compactor::CompactError> {
            Ok(format!("summary of {} messages", req.messages.len()))
        }
    }
    Arc::new(Compactor::new(Arc::new(StubSummarizer)))
}

fn make_state_with_context(tokens: usize, window: usize) -> State {
    let mut state = State::default();
    state.over_view.context_tokens = tokens;
    state.over_view.model_context_window = window;
    state
}

// ---------------------------------------------------------------------------
// [构造] Construction
// ---------------------------------------------------------------------------

#[test]
fn new_uses_defaults() {
    let mw = SummarizationMiddleware::new(make_compactor());
    assert_eq!(mw.trigger_ratio(), DEFAULT_TRIGGER_RATIO);
    assert_eq!(mw.keep_tail(), DEFAULT_KEEP_TAIL);
    assert_eq!(mw.name(), "summarization");
}

#[test]
fn with_trigger_ratio_updates_value() {
    let mw = SummarizationMiddleware::new(make_compactor()).with_trigger_ratio(0.5);
    assert_eq!(mw.trigger_ratio(), 0.5);
}

#[test]
fn with_trigger_ratio_clamps_to_unit_interval() {
    let mw = SummarizationMiddleware::new(make_compactor()).with_trigger_ratio(1.5);
    assert_eq!(mw.trigger_ratio(), 1.0);
    let mw = SummarizationMiddleware::new(make_compactor()).with_trigger_ratio(-0.5);
    assert_eq!(mw.trigger_ratio(), 0.0);
}

#[test]
fn with_keep_tail_updates_value() {
    let mw = SummarizationMiddleware::new(make_compactor()).with_keep_tail(10);
    assert_eq!(mw.keep_tail(), 10);
}

// ---------------------------------------------------------------------------
// [方法] should_compact
// ---------------------------------------------------------------------------

#[test]
fn should_compact_false_when_window_zero() {
    let mw = SummarizationMiddleware::new(make_compactor());
    let state = make_state_with_context(1000, 0);
    assert!(!mw.should_compact(&state));
}

#[test]
fn should_compact_false_when_low_usage() {
    let mw = SummarizationMiddleware::new(make_compactor()).with_trigger_ratio(0.8);
    // 1000 / 10000 = 0.1 < 0.8
    let state = make_state_with_context(1000, 10000);
    assert!(!mw.should_compact(&state));
}

#[test]
fn should_compact_true_when_over_threshold() {
    let mw = SummarizationMiddleware::new(make_compactor()).with_trigger_ratio(0.8);
    // 8500 / 10000 = 0.85 >= 0.8
    let state = make_state_with_context(8500, 10000);
    assert!(mw.should_compact(&state));
}

#[test]
fn should_compact_true_at_exact_threshold() {
    let mw = SummarizationMiddleware::new(make_compactor()).with_trigger_ratio(0.8);
    // 8000 / 10000 = 0.8 >= 0.8 (>=)
    let state = make_state_with_context(8000, 10000);
    assert!(mw.should_compact(&state));
}

#[test]
fn should_compact_false_just_below_threshold() {
    let mw = SummarizationMiddleware::new(make_compactor()).with_trigger_ratio(0.8);
    // 7999 / 10000 = 0.7999 < 0.8
    let state = make_state_with_context(7999, 10000);
    assert!(!mw.should_compact(&state));
}

// ---------------------------------------------------------------------------
// [trait] before_model_call behavior
// ---------------------------------------------------------------------------

#[tokio::test]
async fn before_model_call_no_op_when_under_threshold() {
    let mw = SummarizationMiddleware::new(make_compactor());
    let mut ctx = ModelRequest::new(
        vec![ModelMessage::new("user", "hi")],
        vec![],
    );
    let state = make_state_with_context(100, 10000);
    // Should not panic, no abort.
    mw.before_model_call(&mut ctx, &state).await;
    assert!(ctx.abort.is_none());
    assert_eq!(ctx.messages.len(), 1);
}

#[tokio::test]
async fn before_model_call_logs_when_over_threshold() {
    let mw = SummarizationMiddleware::new(make_compactor()).with_trigger_ratio(0.5);
    let mut ctx = ModelRequest::new(vec![ModelMessage::new("user", "hi")], vec![]);
    let state = make_state_with_context(8000, 10000); // 0.8
    // Should not abort; just log.
    mw.before_model_call(&mut ctx, &state).await;
    assert!(ctx.abort.is_none());
}

#[tokio::test]
async fn before_agent_is_no_op() {
    let mw = SummarizationMiddleware::new(make_compactor());
    let state = make_state_with_context(9000, 10000);
    // Should not panic; v1 implementation has no before_agent logic.
    mw.before_agent(&state).await;
}

// ---------------------------------------------------------------------------
// [trait] Box / Arc compatibility
// ---------------------------------------------------------------------------

#[tokio::test]
async fn can_be_arc_dyn_middleware() {
    let mw = SummarizationMiddleware::new(make_compactor());
    let arc: Arc<dyn arf_core::Middleware> = Arc::new(mw);
    let mut ctx = ModelRequest::new(vec![], vec![]);
    let state = make_state_with_context(0, 10000);
    arc.before_model_call(&mut ctx, &state).await;
    assert!(ctx.abort.is_none());
}

#[tokio::test]
async fn can_be_box_dyn_middleware() {
    let mw = SummarizationMiddleware::new(make_compactor());
    let boxed: Box<dyn arf_core::Middleware> = Box::new(mw);
    assert_eq!(boxed.name(), "summarization");
}

// ---------------------------------------------------------------------------
// [边界] Edge cases
// ---------------------------------------------------------------------------

#[test]
fn should_compact_with_zero_tokens() {
    let mw = SummarizationMiddleware::new(make_compactor()).with_trigger_ratio(0.0);
    let state = make_state_with_context(0, 10000);
    // 0 / 10000 = 0 >= 0 → true (with threshold 0, anything triggers)
    assert!(mw.should_compact(&state));
}

#[test]
fn should_compact_with_full_window() {
    let mw = SummarizationMiddleware::new(make_compactor());
    let state = make_state_with_context(10000, 10000);
    assert!(mw.should_compact(&state));
}