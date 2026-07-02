//! ARF Compactor (Phase 8 task F6).
//!
//! Reduces an Engine's context window by summarizing the oldest messages
//! into a single system message. The summary LLM call is delegated to a
//! caller-provided async closure (so the Compactor stays decoupled from
//! how ModelCall is routed — that decision lives in the App).
//!
//! Triggered by a `CheckpointRule::when_context_over` rule, which the App
//! can construct via the `when_context_over()` helper in `arf-core`.

use std::sync::Arc;

use arf_core::{Checkpoint, CheckpointRule, ModelMessage, State};
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Errors from compaction.
#[derive(Debug, Error)]
pub enum CompactError {
    #[error("llm error: {0}")]
    Llm(String),
    #[error("model response missing summary")]
    NoSummary,
    #[error("serialization error: {0}")]
    Serde(#[from] serde_json::Error),
}

/// Result of a successful compaction.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CompactResult {
    pub summary: String,
    pub before_tokens: usize,
    pub after_tokens: usize,
    pub messages_before: usize,
    pub messages_after: usize,
}

impl CompactResult {
    pub fn token_reduction_pct(&self) -> f64 {
        if self.before_tokens == 0 {
            0.0
        } else {
            100.0 * (1.0 - self.after_tokens as f64 / self.before_tokens as f64)
        }
    }
}

/// LLM call interface for the Compactor. The default implementation routes
/// through the bus via the App; tests can use a mock.
#[async_trait]
pub trait Summarizer: Send + Sync {
    async fn summarize(&self, messages_to_summarize: &[ModelMessage]) -> Result<String, CompactError>;
}

/// Default instruction prepended to the summarization prompt.
pub const DEFAULT_INSTRUCTION: &str = "You are a conversation summarizer. Produce a concise but information-dense summary that preserves task context, decisions made, files involved, and next steps. The summary will replace the original messages in the context window.";

/// Compactor — reduces context window by summarizing old messages.
pub struct Compactor {
    summarizer: Arc<dyn Summarizer>,
    instruction: String,
}

impl Compactor {
    pub fn new(summarizer: Arc<dyn Summarizer>) -> Self {
        Self {
            summarizer,
            instruction: DEFAULT_INSTRUCTION.to_string(),
        }
    }

    pub fn with_instruction(mut self, instruction: impl Into<String>) -> Self {
        self.instruction = instruction.into();
        self
    }

    /// Run a compaction. Mutates `state` in place.
    ///
    /// Strategy: keep the most recent `keep_tail` messages; summarize the rest
    /// into a single system message at index 0.
    pub async fn compact(
        &self,
        state: &mut State,
        keep_tail: usize,
    ) -> Result<CompactResult, CompactError> {
        if state.messages.len() <= keep_tail + 1 {
            // Nothing to compact.
            return Ok(CompactResult {
                summary: String::new(),
                before_tokens: state.over_view.context_tokens,
                after_tokens: state.over_view.context_tokens,
                messages_before: state.messages.len(),
                messages_after: state.messages.len(),
            });
        }

        let messages_before = state.messages.len();
        let before_tokens = state.over_view.context_tokens;

        // Split: [0..split] → summarize, [split..] → keep
        let split = messages_before.saturating_sub(keep_tail);
        let to_summarize: Vec<ModelMessage> = state.messages[..split].to_vec();
        let tail: Vec<ModelMessage> = state.messages[split..].to_vec();

        // Build a synthesized system+user message for the summarizer
        let user_msg = ModelMessage::new(
            "user",
            format!(
                "Please summarize the following conversation ({} messages):\n\n{}",
                to_summarize.len(),
                to_summarize
                    .iter()
                    .map(|m| format!("[{}] {}", m.role, m.content))
                    .collect::<Vec<_>>()
                    .join("\n")
            ),
        );
        let mut messages_for_llm = vec![ModelMessage::new("system", &self.instruction), user_msg];
        let summary = self.summarizer.summarize(&messages_for_llm).await?;
        messages_for_llm.clear();

        // Build new state.messages
        let mut new_msgs: Vec<ModelMessage> = Vec::with_capacity(1 + tail.len());
        new_msgs.push(ModelMessage::new(
            "system",
            format!("[COMPACTED SUMMARY]\n{summary}"),
        ));
        new_msgs.extend(tail);

        // Apply
        state.messages = new_msgs;

        // Update token estimate (rough: assume summary is ~15% of original)
        let after_tokens = ((before_tokens as f64) * 0.15) as usize;
        state.over_view.context_tokens = after_tokens;

        let messages_after = state.messages.len();
        Ok(CompactResult {
            summary,
            before_tokens,
            after_tokens,
            messages_before,
            messages_after,
        })
    }
}

// ── when_context_over CheckpointRule factory ─────────────────────────

/// Phase 8 task F6: build a `CheckpointRule` that fires compaction when
/// `context_utilization() >= ratio`. The rule's `build` closure creates
/// a `CompactRequest` marker message. The App-level Engine handler picks
/// it up and runs `Compactor::compact`.
///
/// `keep_tail` controls how many recent messages are preserved.
pub fn when_context_over(ratio: f64, keep_tail: usize) -> CheckpointRule {
    CheckpointRule::when_context_over(
        "when_context_over",
        Checkpoint::BeforeModelCall,
        ratio,
        move |_state| {
            Box::new(CompactRequest {
                threshold: ratio,
                keep_tail,
            })
        },
    )
}

// ── CompactRequest / CompactDone markers ─────────────────────────────

/// Marker emitted by the `when_context_over` rule. The App's Engine handler
/// processes it by invoking `Compactor::compact`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompactRequest {
    pub threshold: f64,
    pub keep_tail: usize,
}

impl arf_core::ActionMessage for CompactRequest {
    fn msg_type(&self) -> &'static str {
        "compact_request"
    }
    fn correlation_id(&self) -> uuid::Uuid {
        uuid::Uuid::new_v4()
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> arf_core::MessageIntent {
        arf_core::MessageIntent::Command
    }
}

/// Compact done — informational; engine can log it.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompactDone {
    pub result: CompactResult,
}

impl arf_core::ActionMessage for CompactDone {
    fn msg_type(&self) -> &'static str {
        "compact_done"
    }
    fn correlation_id(&self) -> uuid::Uuid {
        uuid::Uuid::new_v4()
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> arf_core::MessageIntent {
        arf_core::MessageIntent::Command
    }
}

// ── Tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// Test summarizer that just concatenates the messages.
    struct ConcatenateSummarizer;

    #[async_trait]
    impl Summarizer for ConcatenateSummarizer {
        async fn summarize(
            &self,
            messages_to_summarize: &[ModelMessage],
        ) -> Result<String, CompactError> {
            Ok(messages_to_summarize
                .iter()
                .map(|m| m.content.clone())
                .collect::<Vec<_>>()
                .join(" | "))
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
        s.over_view.context_tokens = n * 100; // 100 tokens each
        s.over_view.model_context_window = n * 200;
        s
    }

    // [构造] CompactResult::token_reduction_pct 计算正确
    #[test]
    fn compact_result_reduction_pct() {
        let r = CompactResult {
            summary: "x".into(),
            before_tokens: 1000,
            after_tokens: 100,
            messages_before: 10,
            messages_after: 2,
        };
        assert!((r.token_reduction_pct() - 90.0).abs() < 0.01);
    }

    // [边界] CompactResult::token_reduction_pct at 0
    #[test]
    fn compact_result_zero_before_safe() {
        let r = CompactResult {
            summary: String::new(),
            before_tokens: 0,
            after_tokens: 0,
            messages_before: 0,
            messages_after: 0,
        };
        assert_eq!(r.token_reduction_pct(), 0.0);
    }

    // [构造] CompactRequest basic
    #[test]
    fn compact_request_basic() {
        use arf_core::ActionMessage;
        let r = CompactRequest {
            threshold: 0.7,
            keep_tail: 4,
        };
        assert_eq!(r.threshold, 0.7);
        assert_eq!(r.keep_tail, 4);
        assert_eq!(<CompactRequest as ActionMessage>::msg_type(&r), "compact_request");
        assert_eq!(
            <CompactRequest as ActionMessage>::intent(&r),
            arf_core::MessageIntent::Command
        );
    }

    // [序列化] CompactRequest 完整 round-trip
    #[test]
    fn compact_request_serde_roundtrip() {
        let r = CompactRequest {
            threshold: 0.85,
            keep_tail: 8,
        };
        let json = serde_json::to_string(&r).unwrap();
        let back: CompactRequest = serde_json::from_str(&json).unwrap();
        assert_eq!(back.threshold, 0.85);
        assert_eq!(back.keep_tail, 8);
    }

    // [trait] CompactDone 序列化
    #[test]
    fn compact_done_serde() {
        let done = CompactDone {
            result: CompactResult {
                summary: "sum".into(),
                before_tokens: 1000,
                after_tokens: 100,
                messages_before: 10,
                messages_after: 2,
            },
        };
        let json = serde_json::to_string(&done).unwrap();
        let back: CompactDone = serde_json::from_str(&json).unwrap();
        assert_eq!(back.result.summary, "sum");
        assert_eq!(back.result.before_tokens, 1000);
    }

    // [构造] when_context_over 返回 CheckpointRule with correct trigger
    #[test]
    fn when_context_over_builds_rule() {
        let rule = when_context_over(0.7, 4);
        assert_eq!(rule.name, "when_context_over");
        assert_eq!(rule.trigger, Checkpoint::BeforeModelCall);
        let mut s = make_state(10);
        s.over_view.context_tokens = 100;
        s.over_view.model_context_window = 100;
        // utilization = 1.0 > 0.7 → fires
        assert!(rule.fires(&s));
    }

    // [边界] when_context_over 不触发 when utilization 低
    #[test]
    fn when_context_over_does_not_fire_when_low() {
        let rule = when_context_over(0.7, 4);
        let s = make_state(10); // utilization = 1000 / 2000 = 0.5
        assert!(!rule.fires(&s));
    }

    // [方法] Compactor::new 设默认值
    #[test]
    fn compactor_new_default_instruction() {
        let c = Compactor::new(Arc::new(ConcatenateSummarizer));
        assert!(c.instruction.contains("summarizer"));
    }

    // [方法] Compactor::with_instruction 覆盖 instruction
    #[test]
    fn compactor_with_instruction() {
        let c = Compactor::new(Arc::new(ConcatenateSummarizer))
            .with_instruction("custom instructions");
        assert_eq!(c.instruction, "custom instructions");
    }

    // [边界] compact() 在 messages 数 <= keep_tail + 1 时不做事
    #[tokio::test]
    async fn compact_skips_when_too_few_messages() {
        let c = Compactor::new(Arc::new(ConcatenateSummarizer));
        let mut s = make_state(3);
        let r = c.compact(&mut s, 5).await.unwrap();
        assert_eq!(r.messages_before, 3);
        assert_eq!(r.messages_after, 3);
        assert!(r.summary.is_empty());
    }

    // [方法] compact 真正执行：messages_before > keep_tail + 1 → 压缩
    #[tokio::test]
    async fn compact_reduces_messages_and_tokens() {
        let c = Compactor::new(Arc::new(ConcatenateSummarizer));
        let mut s = make_state(10);
        let r = c.compact(&mut s, 3).await.unwrap();
        assert_eq!(r.messages_before, 10);
        // 1 summary + 3 kept = 4
        assert_eq!(r.messages_after, 4);
        // context_tokens dropped to 15% of 1000 = 150
        assert_eq!(s.over_view.context_tokens, 150);
        // The first message is now the summary
        assert!(s.messages[0].content.contains("COMPACTED SUMMARY"));
    }

    // [方法] compact 后只剩 summary + tail
    #[tokio::test]
    async fn compact_preserves_tail_in_order() {
        let c = Compactor::new(Arc::new(ConcatenateSummarizer));
        let mut s = make_state(10);
        let _ = c.compact(&mut s, 3).await.unwrap();
        // The last 3 messages (indices 7,8,9) should be in positions 1,2,3
        assert!(s.messages[1].content.contains("message 7"));
        assert!(s.messages[2].content.contains("message 8"));
        assert!(s.messages[3].content.contains("message 9"));
    }
}
