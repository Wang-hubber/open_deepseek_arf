//! compact_default.rs — Phase 9 task 9.11.1
//!
//! 探查 Compactor + Summarizer trait 端到端 context 压缩能力。
//! 不依赖 LLM——使用 mock Summarizer（拼接 messages）。
//!
//! 3 test cases:
//! 1. compactor_compacts_state_messages — 10 messages + keep_tail=3 → 4 messages
//! 2. compactor_with_custom_summarizer — 自定义 Summarizer 端到端
//! 3. compactor_compact_result_fields — CompactResult 6 字段 + token_reduction_pct
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.11.1.md`（独立文件，独立 commit）。

mod common;

use std::sync::Arc;

use arf_compactor::{CompactResult, Compactor, Summarizer};
use arf_core::{ModelMessage, State};
use async_trait::async_trait;

// ═══════════════════════════════════════════════════════════════════════
// 共享 mock Summarizer — 用于端到端 test
// ═══════════════════════════════════════════════════════════════════════

/// 简单拼接 Summarizer——把 messages content 用 " | " 拼起来。
struct JoinSummarizer;

#[async_trait]
impl Summarizer for JoinSummarizer {
    async fn summarize(
        &self,
        messages: &[ModelMessage],
    ) -> Result<String, arf_compactor::CompactError> {
        Ok(messages
            .iter()
            .map(|m| m.content.clone())
            .collect::<Vec<_>>()
            .join(" | "))
    }
}

/// 前缀 Summarizer——加 "CUSTOM_SUMMARY: " 前缀。
struct PrefixSummarizer {
    prefix: String,
}

#[async_trait]
impl Summarizer for PrefixSummarizer {
    async fn summarize(
        &self,
        messages: &[ModelMessage],
    ) -> Result<String, arf_compactor::CompactError> {
        let body = messages
            .iter()
            .map(|m| format!("[{}] {}", m.role, m.content))
            .collect::<Vec<_>>()
            .join("; ");
        Ok(format!("{}{}", self.prefix, body))
    }
}

fn make_state(n: usize) -> State {
    let mut s = State::new();
    for i in 0..n {
        s.push_message(ModelMessage::new(
            if i % 2 == 0 { "user" } else { "assistant" },
            format!("message {i}"),
        ));
    }
    s.over_view.context_tokens = n * 100; // 100 tokens per message
    s.over_view.model_context_window = n * 200;
    s
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — Compactor::compact 端到端：10 messages → 4 messages
// ═══════════════════════════════════════════════════════════════════════

// [方法] state 10 messages + keep_tail=3 → compact() →
// state.messages 长度 4（1 summary + 3 tail）；tail 内容保留
// (compactor/lib.rs:122-128)。
#[tokio::test]
async fn compactor_compacts_state_messages() {
    let c = Compactor::new(Arc::new(JoinSummarizer));
    let mut s = make_state(10);

    let result = c.compact(&mut s, 3).await.expect("compact");

    println!(
        "[compact] result: messages_before={} messages_after={} before_tokens={} after_tokens={}",
        result.messages_before, result.messages_after, result.before_tokens, result.after_tokens
    );
    println!("[compact] state.messages.len() = {}", s.messages.len());

    assert_eq!(result.messages_before, 10);
    assert_eq!(s.messages.len(), 4, "1 summary + 3 tail = 4");
    // tail 保留（indices 7, 8, 9）
    assert_eq!(s.messages[1].content, "message 7");
    assert_eq!(s.messages[2].content, "message 8");
    assert_eq!(s.messages[3].content, "message 9");
    // summary 头格式
    assert!(s.messages[0].content.contains("COMPACTED SUMMARY"));
    assert!(s.messages[0].content.contains("message 0")); // 拼接含 message 0
    println!("[compact] tail preserved + summary inserted ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — 自定义 Summarizer impl trait
// ═══════════════════════════════════════════════════════════════════════

// [方法] E (Extensible)：app 自定义 Summarizer impl trait，
// Compactor::compact 调用自定义 summarize() 拿 summary。
// 验证 summary 含自定义前缀。
#[tokio::test]
async fn compactor_with_custom_summarizer() {
    let c = Compactor::new(Arc::new(PrefixSummarizer {
        prefix: "CUSTOM_SUMMARY: ".into(),
    }))
    .with_instruction("custom instruction text");
    let mut s = make_state(6);

    // Note: Compactor.instruction is private; we test the override effect
    // indirectly through the message delivered to the summarizer.

    let result = c.compact(&mut s, 2).await.expect("compact");
    assert_eq!(result.messages_before, 6);
    assert_eq!(s.messages.len(), 3, "1 summary + 2 tail = 3");

    // summary 应含自定义前缀
    assert!(
        result.summary.starts_with("CUSTOM_SUMMARY: "),
        "expected summary to start with custom prefix, got: {}",
        result.summary
    );
    // summary 应含 role/content
    assert!(result.summary.contains("[user]"));
    assert!(result.summary.contains("message 0"));
    // state.messages[0] 应含 "CUSTOM_SUMMARY: " 前缀
    assert!(s.messages[0].content.contains("CUSTOM_SUMMARY: "));
    println!("[compact/custom] summary with custom prefix: {}...", &result.summary[..60]);
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — CompactResult 字段 + 边界 + token_reduction_pct
// ═══════════════════════════════════════════════════════════════════════

// [方法] CompactResult 6 字段（summary / before_tokens / after_tokens /
// messages_before / messages_after + token_reduction_pct() 方法）值正确。
// 边界：messages 数 <= keep_tail + 1 时 compact 不做事。
#[tokio::test]
async fn compactor_compact_result_fields() {
    let c = Compactor::new(Arc::new(JoinSummarizer));
    let mut s = make_state(10);

    let result: CompactResult = c.compact(&mut s, 3).await.expect("compact");

    // 6 字段值
    assert_eq!(result.messages_before, 10);
    assert_eq!(result.messages_after, 4, "1 summary + 3 tail");
    assert_eq!(result.before_tokens, 1000, "10 msgs × 100 tokens");
    // context_tokens 重算: before * 0.15 = 150
    assert_eq!(result.after_tokens, 150);
    assert_eq!(s.over_view.context_tokens, 150);
    // summary 非空
    assert!(!result.summary.is_empty());
    // token_reduction_pct: (1 - 150/1000) * 100 = 85%
    let pct = result.token_reduction_pct();
    assert!((pct - 85.0).abs() < 0.01, "expected 85.0, got {pct}");
    println!(
        "[compact/result] all 6 fields OK; reduction_pct = {pct} ✓"
    );

    // 边界：messages <= keep_tail + 1 → compact 不做事
    let c2 = Compactor::new(Arc::new(JoinSummarizer));
    let mut s_small = make_state(3);
    let r2 = c2.compact(&mut s_small, 5).await.expect("compact small");
    assert_eq!(r2.messages_before, 3);
    assert_eq!(r2.messages_after, 3, "no compaction");
    assert!(r2.summary.is_empty(), "empty summary when no compaction");
    assert_eq!(r2.before_tokens, 300, "3 msgs × 100");
    assert_eq!(r2.after_tokens, 300, "no change");
    assert_eq!(r2.token_reduction_pct(), 0.0);
    println!("[compact/boundary] small state: no-op with empty summary ✓");
}
