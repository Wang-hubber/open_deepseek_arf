//! Engine dispatcher (Phase 8 task F2).
//!
//! Decouples message-type-specific handling from `Engine.run()`. The ReAct
//! loop still owns `ModelCall` and `ToolExec` (built-in), but other
//! `ActionMessage` types (SubagentDelegate, PeerMessage, MemoryOp,
//! HumanHandoff, ModelResponseChunk) are handled by registered
//! `MessageHandler` instances via `Engine::dispatch_incoming`.
//!
//! App code registers handlers via `engine.add_handler(Arc<dyn MessageHandler>)`.
//! Default handlers are provided in [`crate::handlers`].

use std::collections::HashMap;
use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{Message, NodeId};

use crate::error::RunError;

/// Outcome of a single handler invocation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HandlerOutcome {
    /// Handler processed the message; do not redeliver.
    Handled,
    /// Handler deferred / ignored the message; Engine may try other handlers.
    Deferred,
}

/// Context passed to handlers. Contains references (no ownership) to the
/// Engine's Bus + identity. State is NOT included — handlers that need to
/// mutate state push messages back onto the bus and let the main loop update.
pub struct HandlerContext<'a> {
    /// Primary bus for sending messages out.
    pub bus: &'a Arc<Bus>,
    /// Engine's own node id.
    pub engine_id: &'a NodeId,
    /// Engine's session id (Phase 8 task F5).
    pub session_id: &'a str,
    /// Bus id of the bus that delivered the message.
    pub from_bus: arf_core::BusId,
}

/// Trait for dispatching incoming Bus messages to type-specific handlers.
pub trait MessageHandler: Send + Sync {
    /// Wire-level `msg_type` this handler is registered for.
    fn msg_type(&self) -> &'static str;

    /// Process one incoming message. Engine calls this from its dispatch loop.
    /// Returning `Ok(Handled)` removes the message from the dispatch queue.
    /// Returning `Err(_)` propagates to the engine loop (which logs + continues).
    fn handle(&self, ctx: &HandlerContext, msg: Message) -> Result<HandlerOutcome, RunError>;
}

/// Registry mapping msg_type → handler. Multiple handlers per type allowed;
/// they're tried in registration order; first Handled wins.
#[derive(Default)]
pub struct HandlerRegistry {
    handlers: HashMap<String, Vec<Arc<dyn MessageHandler>>>,
}

impl HandlerRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a handler for a given `msg_type`. Duplicate registration is allowed
    /// (e.g., a custom App handler layered over a built-in one).
    pub fn register(&mut self, handler: Arc<dyn MessageHandler>) {
        self.handlers
            .entry(handler.msg_type().to_string())
            .or_default()
            .push(handler);
    }

    /// Replace all handlers for a given `msg_type` with a single new handler.
    /// Phase 8 task F2: Engine.add_handler(replace=true) uses this.
    pub fn replace(&mut self, msg_type: &str, handler: Arc<dyn MessageHandler>) {
        assert_eq!(handler.msg_type(), msg_type, "handler.msg_type() must match");
        self.handlers.insert(msg_type.to_string(), vec![handler]);
    }

    /// Dispatch a message: try all registered handlers for its `msg_type`,
    /// in registration order. Returns `Handled` if any handler succeeded;
    /// `Deferred` if no handler matched or all deferred.
    pub fn dispatch(
        &self,
        ctx: &HandlerContext,
        msg: Message,
    ) -> Result<HandlerOutcome, RunError> {
        if let Some(handlers) = self.handlers.get(&msg.msg_type) {
            for h in handlers {
                match h.handle(ctx, msg.clone()) {
                    Ok(HandlerOutcome::Handled) => return Ok(HandlerOutcome::Handled),
                    Ok(HandlerOutcome::Deferred) => continue,
                    Err(e) => return Err(e),
                }
            }
        }
        Ok(HandlerOutcome::Deferred)
    }

    /// Number of registered (msg_type, handler) pairs.
    pub fn len(&self) -> usize {
        self.handlers.values().map(|v| v.len()).sum()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// msg_types currently registered.
    pub fn msg_types(&self) -> Vec<String> {
        self.handlers.keys().cloned().collect()
    }
}

// ── Tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use arf_core::{Message, NodeId};
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct CountHandler {
        ty: &'static str,
        counter: Arc<AtomicUsize>,
    }

    impl MessageHandler for CountHandler {
        fn msg_type(&self) -> &'static str {
            self.ty
        }
        fn handle(&self, _ctx: &HandlerContext, _msg: Message) -> Result<HandlerOutcome, RunError> {
            self.counter.fetch_add(1, Ordering::SeqCst);
            Ok(HandlerOutcome::Handled)
        }
    }

    fn make_msg(ty: &str) -> Message {
        Message::new(ty, NodeId::new("from"), vec![], serde_json::json!({}))
    }

    // [构造] HandlerRegistry::new 为空
    #[test]
    fn registry_new_is_empty() {
        let r = HandlerRegistry::new();
        assert!(r.is_empty());
        assert_eq!(r.len(), 0);
    }

    // [方法] register 后 len 增加
    #[test]
    fn registry_register_increments() {
        let mut r = HandlerRegistry::new();
        r.register(Arc::new(CountHandler {
            ty: "peer_message",
            counter: Arc::new(AtomicUsize::new(0)),
        }));
        assert_eq!(r.len(), 1);
        assert_eq!(r.msg_types(), vec!["peer_message".to_string()]);
    }

    // [方法] 多个 handler 同一 msg_type 都注册（不 replace）
    #[test]
    fn registry_multiple_handlers_same_type() {
        let mut r = HandlerRegistry::new();
        r.register(Arc::new(CountHandler {
            ty: "x",
            counter: Arc::new(AtomicUsize::new(0)),
        }));
        r.register(Arc::new(CountHandler {
            ty: "x",
            counter: Arc::new(AtomicUsize::new(1)),
        }));
        assert_eq!(r.len(), 2);
    }

    // [方法] dispatch 无 handler 时返回 Deferred
    #[tokio::test]
    async fn dispatch_no_handler_returns_deferred() {
        let r = HandlerRegistry::new();
        let bus = arf_bus::Bus::new(std::time::Duration::from_secs(60), std::time::Duration::from_secs(60), 256);
        let bus_arc = Arc::new(bus);
        let eng_id = NodeId::new("e1");
        let ctx = HandlerContext {
            bus: &bus_arc,
            engine_id: &eng_id,
            session_id: "s1",
            from_bus: arf_core::BusId(uuid::Uuid::nil()),
        };
        let outcome = r.dispatch(&ctx, make_msg("nothing")).unwrap();
        assert_eq!(outcome, HandlerOutcome::Deferred);
    }

    // [方法] dispatch 匹配到 handler 时调用 + 返回 Handled
    #[tokio::test]
    async fn dispatch_matching_handler_called() {
        let counter = Arc::new(AtomicUsize::new(0));
        let mut r = HandlerRegistry::new();
        r.register(Arc::new(CountHandler {
            ty: "peer_message",
            counter: counter.clone(),
        }));
        let bus = arf_bus::Bus::new(std::time::Duration::from_secs(60), std::time::Duration::from_secs(60), 256);
        let bus_arc = Arc::new(bus);
        let eng_id = NodeId::new("e1");
        let ctx = HandlerContext {
            bus: &bus_arc,
            engine_id: &eng_id,
            session_id: "s1",
            from_bus: arf_core::BusId(uuid::Uuid::nil()),
        };
        let outcome = r.dispatch(&ctx, make_msg("peer_message")).unwrap();
        assert_eq!(outcome, HandlerOutcome::Handled);
        assert_eq!(counter.load(Ordering::SeqCst), 1);
    }

    // [方法] 多 handler 同一 type：第一个 Handled 胜出，后续不调用
    #[tokio::test]
    async fn dispatch_first_handled_wins() {
        let c1 = Arc::new(AtomicUsize::new(0));
        let c2 = Arc::new(AtomicUsize::new(0));
        let mut r = HandlerRegistry::new();
        r.register(Arc::new(CountHandler {
            ty: "x",
            counter: c1.clone(),
        }));
        r.register(Arc::new(CountHandler {
            ty: "x",
            counter: c2.clone(),
        }));
        let bus = arf_bus::Bus::new(std::time::Duration::from_secs(60), std::time::Duration::from_secs(60), 256);
        let bus_arc = Arc::new(bus);
        let eng_id = NodeId::new("e1");
        let ctx = HandlerContext {
            bus: &bus_arc,
            engine_id: &eng_id,
            session_id: "s1",
            from_bus: arf_core::BusId(uuid::Uuid::nil()),
        };
        let outcome = r.dispatch(&ctx, make_msg("x")).unwrap();
        assert_eq!(outcome, HandlerOutcome::Handled);
        assert_eq!(c1.load(Ordering::SeqCst), 1);
        assert_eq!(c2.load(Ordering::SeqCst), 0);
    }

    // [trait] MessageHandler::msg_type 返回正确字符串
    #[test]
    fn handler_msg_type_returns_self_ty() {
        let h = CountHandler {
            ty: "peer_message",
            counter: Arc::new(AtomicUsize::new(0)),
        };
        assert_eq!(h.msg_type(), "peer_message");
    }
}
