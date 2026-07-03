//! compact_checkpoint_rule.rs — Phase 9 task 9.11.2
//!
//! 探查 `when_context_over` CheckpointRule 端到端行为。
//! 不依赖 LLM——直接测 rule.fires() / build_msg()。
//!
//! 3 test cases:
//! 1. when_context_over_rule_fires_at_high_utilization — state.util=0.8 ≥ 0.7 → fires
//! 2. when_context_over_rule_does_not_fire_at_low_utilization — state.util=0.3 < 0.7 → no fire
//! 3. when_context_over_builds_compact_request_with_correct_fields — build msg_type + payload
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.11.2.md`（独立文件，独立 commit）。

mod common;

use arf_compactor::when_context_over;
use arf_core::{ActionMessage, Checkpoint, ModelMessage, OverView, State};

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — high utilization (0.8) ≥ 0.7 → fires
// ═══════════════════════════════════════════════════════════════════════

// [方法] state.context_tokens=80, model_context_window=100 → util=0.8
// rule.fires(&state) == true。
#[test]
fn when_context_over_rule_fires_at_high_utilization() {
    let rule = when_context_over(0.7, 4);
    let mut s = State::new();
    s.over_view = OverView {
        context_tokens: 80,
        model_context_window: 100,
        ..OverView::default()
    };
    // 加 5 条消息以让 state 非空
    for i in 0..5 {
        s.push_message(ModelMessage::new(
            if i % 2 == 0 { "user" } else { "assistant" },
            format!("m{i}"),
        ));
    }
    assert!(rule.fires(&s), "utilization 0.8 ≥ 0.7 should fire");
    // trigger = BeforeModelCall
    assert_eq!(rule.trigger, Checkpoint::BeforeModelCall);
    // name = "when_context_over"
    assert_eq!(rule.name, "when_context_over");
    println!(
        "[cpr/rule] high util: fires=true trigger={:?} name={}",
        rule.trigger, rule.name
    );
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — low utilization (0.3) < 0.7 → no fire
// ═══════════════════════════════════════════════════════════════════════

// [方法] state.context_tokens=30, model_context_window=100 → util=0.3
// rule.fires(&state) == false。
#[test]
fn when_context_over_rule_does_not_fire_at_low_utilization() {
    let rule = when_context_over(0.7, 4);
    let mut s = State::new();
    s.over_view = OverView {
        context_tokens: 30,
        model_context_window: 100,
        ..OverView::default()
    };
    assert!(!rule.fires(&s), "utilization 0.3 < 0.7 should not fire");
    println!("[cpr/rule] low util: fires=false ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — build_msg 返回 CompactRequest 含正确字段
// ═══════════════════════════════════════════════════════════════════════

// [方法] rule.build_msg(&state) → Box<dyn ActionMessage>；
// msg_type = "compact_request"；payload 含 threshold + keep_tail。
// 验证 compactor/lib.rs:181-194 的 ActionMessage impl。
#[test]
fn when_context_over_builds_compact_request_with_correct_fields() {
    let rule = when_context_over(0.85, 8);
    let s = State::new();
    let msg = rule.build_msg(&s);

    // msg_type
    assert_eq!(msg.msg_type(), "compact_request");
    // intent
    assert_eq!(msg.intent(), arf_core::MessageIntent::Command);
    // payload
    let payload = msg.payload();
    println!("[cpr/rule] build msg: type={} payload={}", msg.msg_type(), payload);
    assert_eq!(payload["threshold"], 0.85);
    assert_eq!(payload["keep_tail"], 8);
    println!("[cpr/rule] build msg: threshold=0.85 keep_tail=8 ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 (Bonus) — boundary: util=0.7 边界 = ratio → fires (>=)
// ═══════════════════════════════════════════════════════════════════════

// [边界] utilization == ratio → >= 关系 → fires。
// core/checkpoint.rs:89 `s.over_view.context_utilization() >= ratio`。
#[test]
fn when_context_over_fires_at_exact_ratio() {
    let rule = when_context_over(0.5, 2);
    let mut s = State::new();
    s.over_view = OverView {
        context_tokens: 50,
        model_context_window: 100,
        ..OverView::default()
    };
    assert!(rule.fires(&s), "utilization 0.5 >= 0.5 should fire (>= relation)");
    println!("[cpr/rule] exact ratio: fires=true ✓");
}
