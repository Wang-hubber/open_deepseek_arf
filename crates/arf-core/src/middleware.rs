//! Middleware trait — request-mutation hooks for the Engine ReAct loop.
//!
//! Distinct from `CheckpointRule`:
//! - CheckpointRule is **declarative** (fire-and-forget, append messages)
//! - Middleware is **wrapping** (mutate the outgoing ModelRequest in place)
//!
//! All hooks are async to allow I/O during middleware execution.

use async_trait::async_trait;
use serde_json::Value;

use crate::State;
use crate::{ModelMessage, ToolSpec};

/// Mutable context for `before_model_call`.
///
/// Middleware can:
/// - Append/prepend messages
/// - Mutate system_prompt_suffix
/// - Add or remove tools
/// - Set abort = true to short-circuit the model call
pub struct ModelRequest {
    /// The full message list (system + memory + skills + conversation).
    pub messages: Vec<ModelMessage>,
    /// Suffix appended to system prompt (after the template).
    pub system_prompt_suffix: String,
    /// Tools visible to the model in this turn.
    pub tools: Vec<ToolSpec>,
    /// Set to `Some(reason)` to abort the model call (no API request made).
    pub abort: Option<String>,
}

impl ModelRequest {
    pub fn new(messages: Vec<ModelMessage>, tools: Vec<ToolSpec>) -> Self {
        Self {
            messages,
            system_prompt_suffix: String::new(),
            tools,
            abort: None,
        }
    }

    /// Append a system message at the end of the existing system messages
    /// (or as the first message if none exist).
    pub fn append_system(&mut self, content: impl Into<String>) {
        let last_sys = self
            .messages
            .iter()
            .rposition(|m| m.role == "system")
            .map(|i| i + 1)
            .unwrap_or(0);
        self.messages
            .insert(last_sys, ModelMessage::new("system", content.into()));
    }

    /// Append to the system_prompt_suffix (concatenated at send time).
    pub fn extend_system_suffix(&mut self, content: impl Into<String>) {
        if !self.system_prompt_suffix.is_empty() {
            self.system_prompt_suffix.push('\n');
        }
        self.system_prompt_suffix.push_str(&content.into());
    }

    /// Add a tool to the model's visible tool list (de-duplicated by name).
    pub fn add_tool(&mut self, tool: ToolSpec) {
        if !self.tools.iter().any(|t| t.name == tool.name) {
            self.tools.push(tool);
        }
    }

    /// Remove a tool from the visible tool list.
    pub fn remove_tool(&mut self, name: &str) {
        self.tools.retain(|t| t.name != name);
    }
}

/// `Middleware` trait — request-mutation hooks in the ReAct loop.
///
/// Implementations are stored as `Vec<Arc<dyn Middleware>>` in
/// `EngineConfig.middlewares` and run in order during each model call.
#[async_trait]
pub trait Middleware: Send + Sync {
    /// Identifier for logging / debugging.
    fn name(&self) -> &str;

    /// Called once at agent startup, before any round. Use for one-shot
    /// setup (load files, register tools, etc.).
    async fn before_agent(&self, _state: &State) {}

    /// Called before each `ModelCall` is sent. Mutates `ctx` in place.
    ///
    /// This is the primary hook for FilesystemMiddleware / MemoryMiddleware
    /// / SummarizationMiddleware — they inject tools, append system
    /// context, or summarize conversation here.
    async fn before_model_call(&self, _ctx: &mut ModelRequest, _state: &State) {}

    /// Called after the model responds. Use for response inspection,
    /// rubric scoring, log enrichment. Default: no-op.
    async fn after_model_call(&self, _state: &State, _response: &Value) {}

    /// Called once at agent shutdown (after final round). Default: no-op.
    async fn after_agent(&self, _state: &State, _final_output: &str) {}
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ModelMessage;
    use serde_json::json;

    fn empty_state() -> State {
        State::default()
    }

    // ---- ModelRequest ------------------------------------------------------

    #[test]
    fn model_request_new_initializes_fields() {
        let req = ModelRequest::new(vec![ModelMessage::new("user", "hi")], vec![]);
        assert_eq!(req.messages.len(), 1);
        assert!(req.system_prompt_suffix.is_empty());
        assert!(req.tools.is_empty());
        assert!(req.abort.is_none());
    }

    #[test]
    fn append_system_inserts_after_last_system() {
        let mut req = ModelRequest::new(
            vec![
                ModelMessage::new("system", "sys1"),
                ModelMessage::new("user", "u1"),
            ],
            vec![],
        );
        req.append_system("sys2");
        assert_eq!(req.messages.len(), 3);
        assert_eq!(req.messages[0].role, "system");
        assert_eq!(req.messages[1].role, "system");
        assert_eq!(req.messages[1].content, "sys2");
        assert_eq!(req.messages[2].role, "user");
    }

    #[test]
    fn append_system_with_no_existing_system_prepends() {
        let mut req = ModelRequest::new(vec![ModelMessage::new("user", "u1")], vec![]);
        req.append_system("first-sys");
        assert_eq!(req.messages.len(), 2);
        assert_eq!(req.messages[0].role, "system");
    }

    #[test]
    fn extend_system_suffix_concatenates() {
        let mut req = ModelRequest::new(vec![], vec![]);
        req.extend_system_suffix("first");
        req.extend_system_suffix("second");
        assert_eq!(req.system_prompt_suffix, "first\nsecond");
    }

    #[test]
    fn add_tool_deduplicates() {
        let mut req = ModelRequest::new(
            vec![],
            vec![ToolSpec::new("read", "Read a file", json!({}))],
        );
        req.add_tool(ToolSpec::new("read", "duplicate", json!({})));
        assert_eq!(req.tools.len(), 1);
        req.add_tool(ToolSpec::new("write", "Write", json!({})));
        assert_eq!(req.tools.len(), 2);
    }

    #[test]
    fn remove_tool_filters() {
        let mut req = ModelRequest::new(
            vec![],
            vec![
                ToolSpec::new("read", "Read", json!({})),
                ToolSpec::new("write", "Write", json!({})),
            ],
        );
        req.remove_tool("read");
        assert_eq!(req.tools.len(), 1);
        assert_eq!(req.tools[0].name, "write");
    }

    // ---- Middleware trait default impls ------------------------------------

    struct NoopMiddleware;

    #[async_trait]
    impl Middleware for NoopMiddleware {
        fn name(&self) -> &str {
            "noop"
        }
    }

    #[tokio::test]
    async fn noop_middleware_default_impls_are_no_op() {
        let mw = NoopMiddleware;
        let mut ctx = ModelRequest::new(vec![ModelMessage::new("user", "hi")], vec![]);
        let state = empty_state();
        mw.before_agent(&state).await;
        mw.before_model_call(&mut ctx, &state).await;
        mw.after_model_call(&state, &json!({})).await;
        mw.after_agent(&state, "final").await;
        assert_eq!(ctx.messages.len(), 1);
        assert!(ctx.abort.is_none());
    }

    // ---- Counting middleware ----------------------------------------------

    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    struct CountingMiddleware {
        before_agent_count: Arc<AtomicUsize>,
        before_model_count: Arc<AtomicUsize>,
        after_model_count: Arc<AtomicUsize>,
        after_agent_count: Arc<AtomicUsize>,
        system_text: &'static str,
    }

    #[async_trait]
    impl Middleware for CountingMiddleware {
        fn name(&self) -> &str {
            "counter"
        }
        async fn before_agent(&self, _state: &State) {
            self.before_agent_count.fetch_add(1, Ordering::SeqCst);
        }
        async fn before_model_call(&self, ctx: &mut ModelRequest, _state: &State) {
            self.before_model_count.fetch_add(1, Ordering::SeqCst);
            ctx.append_system(self.system_text);
        }
        async fn after_model_call(&self, _state: &State, _response: &Value) {
            self.after_model_count.fetch_add(1, Ordering::SeqCst);
        }
        async fn after_agent(&self, _state: &State, _final_output: &str) {
            self.after_agent_count.fetch_add(1, Ordering::SeqCst);
        }
    }

    #[tokio::test]
    async fn middleware_can_modify_request() {
        let mw = CountingMiddleware {
            before_agent_count: Arc::new(AtomicUsize::new(0)),
            before_model_count: Arc::new(AtomicUsize::new(0)),
            after_model_count: Arc::new(AtomicUsize::new(0)),
            after_agent_count: Arc::new(AtomicUsize::new(0)),
            system_text: "injected",
        };
        let mut ctx = ModelRequest::new(vec![ModelMessage::new("user", "hi")], vec![]);
        let state = empty_state();
        mw.before_model_call(&mut ctx, &state).await;
        assert_eq!(mw.before_model_count.load(Ordering::SeqCst), 1);
        // appended system message at the front (no existing system)
        assert_eq!(ctx.messages[0].role, "system");
        assert_eq!(ctx.messages[0].content, "injected");
    }

    #[tokio::test]
    async fn middleware_can_abort() {
        struct AbortMiddleware;
        #[async_trait]
        impl Middleware for AbortMiddleware {
            fn name(&self) -> &str {
                "abort"
            }
            async fn before_model_call(&self, ctx: &mut ModelRequest, _state: &State) {
                ctx.abort = Some("blocked by policy".into());
            }
        }
        let mw = AbortMiddleware;
        let mut ctx = ModelRequest::new(vec![], vec![]);
        let state = empty_state();
        mw.before_model_call(&mut ctx, &state).await;
        assert!(ctx.abort.is_some());
    }
}