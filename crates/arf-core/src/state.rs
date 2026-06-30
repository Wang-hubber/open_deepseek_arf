//! State — Engine-owned Agent state (Phase 6 §1.6).
//!
//! App holds `State` and lends `&mut` to `Engine.run()`. Persistence is
//! App's concern (snapshot via `Engine.snapshot()`, restore via `Engine.restore()`).

use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::{ModelMessage, WaitEvent};

/// Aggregate metrics (O(1) read). Phase 6 §1.6.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OverView {
    /// Total `chat()` rounds in this session.
    pub round_count: usize,
    /// Total ReAct transfer steps (model_call + tool_exec).
    pub turn_count: usize,
    /// Latest LLM-reported prompt token count. Updated from `model_response.usage.prompt_tokens`.
    pub context_tokens: usize,
    /// Model's context window (from ModelAdapter capabilities at startup).
    pub model_context_window: usize,
    /// Cumulative active time in `processing` state.
    pub runtime: Duration,
    /// Most recent user input (for quick access in tests/diagnostics).
    pub last_user_message: String,
}

impl OverView {
    /// Context-token utilization as a fraction in [0.0, 1.0+].
    pub fn context_utilization(&self) -> f64 {
        if self.model_context_window == 0 {
            0.0
        } else {
            self.context_tokens as f64 / self.model_context_window as f64
        }
    }
}

/// Engine state. App-owned; Engine borrows `&mut` during `run()`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct State {
    /// Conversation history (App-readable).
    pub messages: Vec<ModelMessage>,
    /// Aggregate metrics.
    pub over_view: OverView,
    /// Pending WaitEvents (Engine-internal; App should not touch).
    pub wait_events: Vec<WaitEvent>,
}

impl State {
    pub fn new() -> Self {
        Self::default()
    }

    /// Append a model-role message and update `over_view`.
    pub fn push_message(&mut self, msg: ModelMessage) {
        if msg.role == "user" {
            self.over_view.last_user_message = msg.content.clone();
        }
        self.messages.push(msg);
    }

    /// Increment round counter (called once per `chat()` round).
    pub fn inc_round(&mut self) {
        self.over_view.round_count += 1;
    }

    /// Increment turn counter (ReAct transfer step).
    pub fn inc_turn(&mut self) {
        self.over_view.turn_count += 1;
    }

    /// Update `context_tokens` from a model_response usage payload.
    /// Phase 6 §1.6: most precise source of token count.
    pub fn set_context_tokens(&mut self, tokens: usize) {
        self.over_view.context_tokens = tokens;
    }
}
