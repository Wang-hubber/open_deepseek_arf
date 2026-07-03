//! compact_custom_summarizer.rs — Phase 9 task 9.11.3
//!
//! 探查自定义 Summarizer 的扩展性 + 错误路径。
//! 不依赖 LLM——使用 mock Summarizer（多种策略）。
//!
//! 3 test cases:
//! 1. truncate_summarizer_keeps_first_n_chars — TruncateSummarizer 截断策略
//! 2. bullet_point_summarizer_formats_per_role — BulletPointSummarizer 格式化
//! 3. error_summarizer_propagates_error — ErrorSummarizer 错误路径 + state 未变
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.11.3.md`（独立文件，独立 commit）。

mod common;

use std::sync::Arc;

use arf_compactor::{CompactError, CompactionRequest, Compactor, Summarizer};
use arf_core::{ModelMessage, State};
use async_trait::async_trait;

// ═══════════════════════════════════════════════════════════════════════
// 自定义 Summarizer 1 — 截断
// ═══════════════════════════════════════════════════════════════════════

struct TruncateSummarizer {
    max_chars: usize,
}

#[async_trait]
impl Summarizer for TruncateSummarizer {
    async fn summarize(
        &self,
        req: CompactionRequest<'_>,
    ) -> Result<String, CompactError> {
        let body = req
            .messages
            .iter()
            .map(|m| m.content.as_str())
            .collect::<Vec<_>>()
            .join(" ");
        Ok(body.chars().take(self.max_chars).collect::<String>())
    }
}

// ═══════════════════════════════════════════════════════════════════════
// 自定义 Summarizer 2 — bullet point 格式
// ═══════════════════════════════════════════════════════════════════════

struct BulletPointSummarizer;

#[async_trait]
impl Summarizer for BulletPointSummarizer {
    async fn summarize(
        &self,
        req: CompactionRequest<'_>,
    ) -> Result<String, CompactError> {
        // F-015: the summarizer now receives the RAW conversation directly, so
        // it formats each message as "• role: content" without reverse-parsing
        // a pre-baked prompt.
        let bullets: Vec<String> = req
            .messages
            .iter()
            .map(|m| format!("• {}: {}", m.role, m.content))
            .collect();
        Ok(bullets.join("\n"))
    }
}

// ═══════════════════════════════════════════════════════════════════════
// 自定义 Summarizer 3 — 错误路径
// ═══════════════════════════════════════════════════════════════════════

struct ErrorSummarizer {
    msg: String,
}

#[async_trait]
impl Summarizer for ErrorSummarizer {
    async fn summarize(
        &self,
        _req: CompactionRequest<'_>,
    ) -> Result<String, CompactError> {
        Err(CompactError::Llm(self.msg.clone()))
    }
}

fn make_state(n: usize) -> State {
    let mut s = State::new();
    for i in 0..n {
        s.push_message(ModelMessage::new(
            if i % 2 == 0 { "user" } else { "assistant" },
            format!("this is message number {i} with some content"),
        ));
    }
    s.over_view.context_tokens = n * 100;
    s.over_view.model_context_window = n * 200;
    s
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — TruncateSummarizer 截断
// ═══════════════════════════════════════════════════════════════════════

// [方法] 自定义 TruncateSummarizer{max_chars=50} → summary 长度 ≤ 50。
#[tokio::test]
async fn truncate_summarizer_keeps_first_n_chars() {
    let c = Compactor::new(Arc::new(TruncateSummarizer { max_chars: 50 }));
    let mut s = make_state(8);
    let original_messages_len = s.messages.len();
    let original_context_tokens = s.over_view.context_tokens;

    let result = c.compact(&mut s, 2).await.expect("compact");
    println!(
        "[custom/truncate] summary.len()={} (max 50), state.messages.len()={}",
        result.summary.len(),
        s.messages.len()
    );
    assert!(result.summary.len() <= 50, "summary must be <= 50 chars, got {}", result.summary.len());
    assert_eq!(s.messages.len(), 3, "1 summary + 2 tail");
    // state messages 数量变了（压缩）
    assert_ne!(s.messages.len(), original_messages_len);
    // context_tokens 重新计算
    assert_ne!(s.over_view.context_tokens, original_context_tokens);
    println!("[custom/truncate] summary length OK, state compressed ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — BulletPointSummarizer 格式化
// ═══════════════════════════════════════════════════════════════════════

// [方法] 自定义 BulletPointSummarizer → summary 含 "• role: content" 格式。
#[tokio::test]
async fn bullet_point_summarizer_formats_per_role() {
    let c = Compactor::new(Arc::new(BulletPointSummarizer));
    let mut s = make_state(6);

    let result = c.compact(&mut s, 2).await.expect("compact");
    println!("[custom/bullet] summary:\n{}", result.summary);
    // summary 应含 "• user:" 或 "• assistant:"
    assert!(result.summary.contains("• user:"), "expected '• user:' in summary");
    assert!(result.summary.contains("• assistant:"), "expected '• assistant:' in summary");
    // 应含 message 0
    assert!(result.summary.contains("message number 0"));
    println!("[custom/bullet] bullet format with role prefix ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — ErrorSummarizer 错误路径
// ═══════════════════════════════════════════════════════════════════════

// [方法] 自定义 ErrorSummarizer 永远返 Err(CompactError::Llm("...")) →
// Compactor::compact 透传错误，state **未**被修改（未半成品）。
#[tokio::test]
async fn error_summarizer_propagates_error() {
    let c = Compactor::new(Arc::new(ErrorSummarizer {
        msg: "simulated LLM outage".into(),
    }));
    let mut s = make_state(8);
    let original_messages = s.messages.clone();
    let original_context_tokens = s.over_view.context_tokens;

    let err = c.compact(&mut s, 3).await.unwrap_err();
    println!("[custom/error] error: {err:?}");
    match err {
        CompactError::Llm(m) => assert_eq!(m, "simulated LLM outage"),
        other => panic!("expected Llm, got {other:?}"),
    }
    // 关键：state **未**被修改（半成品状态）
    assert_eq!(s.messages, original_messages, "state.messages should be unchanged");
    assert_eq!(s.over_view.context_tokens, original_context_tokens);
    println!("[custom/error] state preserved on error (no half-product) ✓");
}
