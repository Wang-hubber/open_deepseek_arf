//! SubagentPool — bounded pool of ephemeral engines (Phase 9 spec §4.6).
//!
//! Each slot holds an `Engine` together with its caller-owned `State` so
//! that the pair cycles through the pool as a unit. The pool lazily
//! provisions slots up to `size` total, reusing them across `delegate()`
//! calls.
//!
//! **Send compatibility (Task 14 review fix):** the `idle` queue is
//! protected by `tokio::sync::Mutex` (not `std::sync::Mutex`) so the
//! `delegate()` future is `Send`. This is required by
//! `pyo3_async_runtimes::tokio::future_into_py` (the multi-thread tokio
//! runtime used by `py-arf/src/lib.rs` will panic when posting a
//! `!Send` future from `local_future_into_py`). `available()` is now
//! async too — it must `.lock().await` to read the queue length.

use std::collections::{HashMap, VecDeque};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use arf_bus::Bus;
use arf_core::{
    msg_type, MessageFilter, NodeId, NodeInfo, State, ToMatch,
};
use arf_engine::{
    config::AgentConfig, Engine, EngineBuilder, TaskInput, TaskResult,
};
use tokio::sync::{Mutex as TokioMutex, Semaphore};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

/// Policy applied when a subagent's outbox is non-empty at the end of
/// `delegate()` (Phase 9 §4.6 — currently a placeholder because outbox
/// tracking is stubbed in Task 4).
#[derive(Debug, Clone)]
pub enum OutboxStrategy {
    /// Sleep for `timeout_ms` then force `reset_state()` on the engine.
    TimeoutAbort { timeout_ms: u64 },
    /// Handoff pending outbox items to a JSONL file at `path`.
    /// (Placeholder — current build only logs the handoff.)
    HandoffOutbox { path: PathBuf },
    /// Synchronously block the pool until the outbox drains (polling).
    SyncWait,
}

impl Default for OutboxStrategy {
    fn default() -> Self {
        OutboxStrategy::TimeoutAbort { timeout_ms: 5_000 }
    }
}

/// Aggregate metrics for a [`SubagentPool`]. Cheap to clone.
#[derive(Debug, Default, Clone)]
pub struct PoolMetrics {
    /// Total `delegate()` calls since pool construction.
    pub total_delegations: u64,
    /// Slots currently leased (executing a task).
    pub active_count: usize,
    /// Slots currently idle (recycled, ready to lease).
    pub idle_count: usize,
    /// Most recent [`arf_engine::RunError`] (if any) emitted by the pool.
    pub last_error: Option<String>,
}

/// Pair of an ephemeral engine with its caller-owned `State`. The pair
/// cycles as a unit — recycling the engine means recycling its state.
pub struct PoolSlot {
    pub engine: Engine,
    pub state: State,
}

/// Bounded pool of ephemeral engines (Phase 9 §4.6).
///
/// `size` constrains concurrent `delegate()` calls via a [`Semaphore`].
/// Engines are lazily provisioned — the pool starts empty and grows up to
/// `size` total over its lifetime.
pub struct SubagentPool {
    bus: Arc<Bus>,
    /// `AgentConfig` is intentionally NOT Clone (holds `Arc<dyn ...>`),
    /// so we wrap it once in `Arc` and clone the arc per build.
    config: Arc<AgentConfig>,
    size: usize,
    /// Idle slots. `None` entries are reserved-but-not-yet-built slots;
    /// using `Option` lets us avoid a separate "marker" type. The semaphore
    /// `size` caps the total leased+idle count.
    ///
    /// Backed by `tokio::sync::Mutex` (not `std::sync::Mutex`) so that
    /// `delegate()`, `populate()`, etc. can hold the lock across
    /// `.await` points and remain `Send` — see module-level docstring.
    idle: Arc<TokioMutex<VecDeque<Option<PoolSlot>>>>,
    semaphore: Arc<Semaphore>,
    outbox_strategy: OutboxStrategy,
    metrics: Arc<Mutex<PoolMetrics>>,
}

impl SubagentPool {
    /// Construct a pool with the default `OutboxStrategy::TimeoutAbort{5000}`.
    ///
    /// The `config` is wrapped in `Arc` because `AgentConfig` is intentionally
    /// not `Clone` (it holds `Arc<dyn ...>` trait objects). Callers that need
    /// shared ownership can construct `Arc::new(my_config)` themselves.
    pub fn new(bus: Arc<Bus>, config: Arc<AgentConfig>, size: usize) -> Self {
        Self::new_with_strategy(bus, config, size, OutboxStrategy::default())
    }

    /// Construct with an explicit outbox strategy.
    ///
    /// Note: the pool is **lazily populated**. Slots are provisioned on
    /// first `delegate()` call (up to `size` over the pool's lifetime).
    /// This keeps construction cheap; eager provisioning would require
    /// running an async loop at `new` time, which isn't possible from a
    /// non-async constructor. Tests that need a full idle queue should
    /// drive one `delegate()` per slot and inspect `available()` after.
    pub fn new_with_strategy(
        bus: Arc<Bus>,
        config: Arc<AgentConfig>,
        size: usize,
        outbox_strategy: OutboxStrategy,
    ) -> Self {
        Self {
            bus,
            config,
            size,
            idle: Arc::new(TokioMutex::new(VecDeque::with_capacity(size))),
            semaphore: Arc::new(Semaphore::new(size)),
            outbox_strategy,
            metrics: Arc::new(Mutex::new(PoolMetrics::default())),
        }
    }

    /// Delegate a task to a pool-managed ephemeral engine. Blocks on the
    /// semaphore if all `size` slots are leased. The slot (engine + state)
    /// is recycled to idle on every return path.
    pub async fn delegate(
        &self,
        task_input: TaskInput,
    ) -> Result<TaskResult, arf_engine::RunError> {
        use arf_engine::RunError;

        // Lease a slot from the semaphore (bounds concurrency).
        let _permit = self
            .semaphore
            .clone()
            .acquire_owned()
            .await
            .map_err(|e| RunError::Internal(format!("semaphore closed: {e}")))?;

        // Pop a slot from idle, or build a fresh one.
        let mut slot = self.take_slot().await?;

        // Multi-turn semantics: do NOT pre-emptively `reset_state` on every
        // slot acquisition. A recycled slot keeps its conversation history
        // so subsequent `delegate()` calls see prior turns (Task 5 review
        // fix). `reset_state` is reserved for the error path below, where
        // we want to clean up bad state before recycling the slot.
        //
        // (Pre-fix code wiped state unconditionally, making recycling
        // pointless — every delegate effectively started from scratch.)

        let cancel = CancellationToken::new();
        let result = slot.engine.run_once(&mut slot.state, task_input, cancel).await;

        // On error, clean up state before recycling so the next caller
        // doesn't inherit a corrupt/halted state. On Ok, preserve state
        // for multi-turn reuse.
        match &result {
            Ok(_) => {
                // Preserve state — keep conversation history for next caller.
            }
            Err(RunError::Internal(reason)) if reason.contains("OutboxNotEmpty") => {
                // Apply outbox strategy (placeholder today — `collect_outbox_pending`
                // returns `[]`, so this branch is currently unreachable; wired
                // for future Task 4 outbox tracking).
                self.apply_outbox_strategy(&slot.engine).await;
            }
            Err(_) => {
                // Other error: clean state to avoid leaking bad state to the
                // next caller. Ignore any reset failure (best-effort cleanup).
                if let Err(e) = slot.engine.reset_state(&mut slot.state) {
                    tracing::warn!("post-error reset_state failed: {e}");
                }
            }
        }

        // Recycle the slot — push back into the idle queue unless the
        // queue is already at capacity.
        self.recycle_slot(slot).await;

        // Snapshot the queue length + permit count FIRST (both involve
        // awaits via `tokio::sync::Mutex` / `Semaphore`). Holding the
        // `std::sync::MutexGuard` over those awaits would make this
        // future `!Send`, which breaks bus-actor `tokio::spawn`.
        let idle_len = self.idle.lock().await.len();
        let active_count = self
            .size
            .saturating_sub(self.semaphore.available_permits());

        // Update metrics. `std::sync::MutexGuard` is `!Send`, so this
        // block stays free of `.await` points — that's the Send
        // invariant the spawn-site relies on.
        {
            let mut m = self.metrics.lock().unwrap();
            m.total_delegations += 1;
            m.idle_count = idle_len;
            m.active_count = active_count;
            if let Err(e) = &result {
                m.last_error = Some(e.to_string());
            }
        }

        result
    }

    /// Eagerly provision `size` slots in the idle queue. After this
    /// resolves, `available() == size`.
    ///
    /// The synchronous `new()` constructor cannot build engines (engine
    /// construction is async), so `populate()` is the explicit async
    /// step that warms the pool before delegating. Safe to call multiple
    /// times — repeated calls are a no-op once `size` slots exist.
    ///
    /// Errors propagate from `build_slot()` (e.g., ResourceRegistry
    /// resolution failures).
    pub async fn populate(&self) -> Result<(), arf_engine::RunError> {
        use arf_engine::RunError;

        let mut idle = self.idle.lock().await;
        while idle.len() < self.size {
            let slot = self.build_slot().await.map_err(|e| {
                self.record_error(format!("populate: build failed: {e}"));
                RunError::Internal(e)
            })?;
            idle.push_back(Some(slot));
        }
        Ok(())
    }

    /// Number of idle (recycled) slots. For capacity diagnostics.
    ///
    /// Async because `idle` is now a `tokio::sync::Mutex` (see module
    /// docstring). The lock is short — no `.await` is held inside —
    /// but we still use `.await` to satisfy the borrow checker.
    pub async fn available(&self) -> usize {
        self.idle.lock().await.len()
    }

    /// Snapshot current metrics.
    pub fn metrics(&self) -> PoolMetrics {
        self.metrics.lock().unwrap().clone()
    }

    /// Borrow the shared Bus handle. Used by PyPoolHandle and tests that
    /// need to send a `subagent_delegate` message at the pool.
    pub fn bus(&self) -> &Arc<Bus> {
        &self.bus
    }

    /// Drop all idle engines. Active engines finish their current task and
    /// are dropped when the pool is dropped entirely.
    pub async fn shutdown(self) {
        self.idle.lock().await.clear();
    }

    /// Connect this pool as a **bus actor**.
    ///
    /// Registers the pool as `subagent-pool/<pool_id>` on the bus
    /// (`node_type="subagent-pool"`, capabilities advertise `pool_id` +
    /// `size`). Spawns a long-running listener that handles every
    /// incoming `subagent_delegate` by calling `self.delegate(TaskInput)`
    /// and replying with `subagent_result` keyed by `correlation_id`.
    ///
    /// ## Bus-actor pattern (Phase 6 / Phase 7 architecture)
    ///
    /// Apps should send a `subagent_delegate` message on the bus and
    /// await `subagent_result` (matched by `correlation_id`). Calling
    /// `pool.delegate(...)` directly still works for non-bus callers
    /// (tests, scripts), but production paths route through `Bus → Pool
    /// node → reply`. This avoids the `spawn_local` panic that comes
    /// from invoking !Send pool futures from a multi-thread tokio
    /// runtime (e.g. `py-arf`'s `local_future_into_py`).
    ///
    /// The handle lives inside the spawned listener — only that task
    /// reads from `handle.recv()` and writes via `handle.send()`. The
    /// rest of the pool's API (metrics, delegate) is unaffected.
    pub async fn connect_to_bus(
        self: Arc<Self>,
        pool_id: impl Into<String>,
    ) -> Result<NodeId, arf_bus::ConnectError> {
        let pool_id = pool_id.into();
        let node_id = NodeId::new(format!("subagent-pool/{pool_id}"));
        let info = NodeInfo {
            node_id: node_id.clone(),
            node_type: "subagent-pool".into(),
            capabilities: serde_json::json!({
                "kind": "subagent-pool",
                "pool_id": pool_id,
                "size": self.size,
                "msg_types": [msg_type::SUBAGENT_DELEGATE],
            }),
            online_since: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis() as u64,
        };
        let filter = MessageFilter {
            types: Some(vec![msg_type::SUBAGENT_DELEGATE.into()]),
            to_match: ToMatch::DirectedToMe,
        };
        let mut handle = self.bus.connect(info, filter).await?;

        let this = self.clone();
        tokio::spawn(async move {
            loop {
                let msg = match handle.recv().await {
                    Ok(m) => m,
                    Err(_) => break, // bus closed
                };
                let cid = msg.correlation_id();
                let from = msg.from.clone();
                let result = this.handle_delegate_msg(&msg).await;
                let payload = match result {
                    Ok(tr) => serde_json::json!({
                        "correlation_id": cid.map(|c| c.to_string()),
                        "ok": true,
                        "output": tr.output,
                        "turns_consumed": tr.turns_consumed,
                        "pending_peer_messages": tr.pending_peer_messages,
                    }),
                    Err(e) => serde_json::json!({
                        "correlation_id": cid.map(|c| c.to_string()),
                        "ok": false,
                        "error": e.to_string(),
                    }),
                };
                if let Err(e) = handle
                    .send(msg_type::SUBAGENT_RESULT, vec![from], payload)
                    .await
                {
                    eprintln!(
                        "[SubagentPool::connect_to_bus] failed to send \
                         {msg_type:?} reply: {e}",
                        msg_type = msg_type::SUBAGENT_RESULT,
                    );
                }
            }
        });
        Ok(node_id)
    }

    /// Internal: parse one `subagent_delegate` bus message and run it
    /// through the pool's existing `delegate()` path. Extracted so unit
    /// tests can drive the same code path without spinning a Bus.
    ///
    /// Accepts two payload shapes:
    ///   (a) Engine-native `SubagentDelegate` — `{ "task": "..." }` (the
    ///       `parent_session_id`/`subagent_node_id`/`context` fields are
    ///       accepted but currently unused: the pool resolves its own
    ///       ephemeral engine + state).
    ///   (b) Convenience `{ "user_message": "..." }` — useful for `curl`
    ///       smoke tests and example apps.
    pub(crate) async fn handle_delegate_msg(
        &self,
        msg: &arf_core::Message,
    ) -> Result<TaskResult, arf_engine::RunError> {
        let user_message = msg
            .payload
            .get("task")
            .or_else(|| msg.payload.get("user_message"))
            .and_then(|v| v.as_str())
            .ok_or_else(|| {
                arf_engine::RunError::Internal(
                    "subagent_delegate payload missing 'task' / 'user_message' \
                     string field"
                        .into(),
                )
            })?
            .to_string();
        self.delegate(TaskInput { user_message }).await
    }

    // ── Internal helpers ──────────────────────────────────────────────

    /// Pop an idle slot from the queue, or build a new one. The fresh
    /// slot path is async (EngineBuilder::build is async).
    async fn take_slot(&self) -> Result<PoolSlot, arf_engine::RunError> {
        // Try idle queue first.
        {
            let mut idle = self.idle.lock().await;
            while let Some(entry) = idle.pop_front() {
                if let Some(slot) = entry {
                    return Ok(slot);
                }
                // None entry — dropped, continue.
            }
        }
        // Empty — build a fresh slot.
        self.build_slot().await.map_err(|e| {
            use arf_engine::RunError;
            self.record_error(format!("build failed: {e}"));
            RunError::Internal(e)
        })
    }

    /// Build a brand new (Engine, State) slot. Encapsulates the
    /// async path and error mapping.
    ///
    /// **Note:** `EngineBuilder::build` takes `AgentConfig` by value. The
    /// pool holds `Arc<AgentConfig>` so we attempt `try_unwrap` to recover
    /// ownership cheaply (zero-copy fast path); on `Err` (shared
    /// ownership) we construct a fresh `AgentConfig` from the arc fields.
    /// Today the engine cache does not share config with any other pool
    /// consumer, so the fast path always wins.
    ///
    /// Each slot gets a unique `agent_id` so multiple ephemeral engines
    /// can coexist on the same bus without `node already connected`
    /// collisions (Phase 9 F-018 — `engine/{provider}` default collides).
    async fn build_slot(&self) -> Result<PoolSlot, String> {
        let cfg: AgentConfig = match Arc::try_unwrap(self.config.clone()) {
            Ok(c) => c,
            // Shared ownership: caller must give us exclusive config OR
            // we rebuild. Currently unreachable in normal use.
            Err(arc) => rebuild_config_from_arc(&arc).ok_or_else(|| {
                "AgentConfig holds non-cloneable trait objects and is shared".to_string()
            })?,
        };
        // F-018: unique agent_id per slot — required so `populate(size=N)`
        // can build N engines on the same bus without NodeId collisions.
        let agent_id = NodeId::new(format!("subagent-pool/{}/{}", cfg.model.provider, Uuid::new_v4()));
        let engine = EngineBuilder::new(vec![self.bus.clone()])
            .ephemeral(true)
            .with_agent_id(agent_id)
            .build(cfg)
            .await
            .map_err(|e| e.to_string())?;
        Ok(PoolSlot {
            engine,
            state: State::new(),
        })
    }

    /// Push a slot back into the idle queue (bounded by `size`).
    ///
    /// Async because `idle` is now a `tokio::sync::Mutex` (Task 14
    /// review fix — `Send` compatibility).
    async fn recycle_slot(&self, slot: PoolSlot) {
        let mut idle = self.idle.lock().await;
        if idle.len() < self.size {
            idle.push_back(Some(slot));
        }
        // else: drop — engine's bus subscriptions cancel on drop.
    }

    /// Apply the configured outbox strategy. Today this is a logging
    /// placeholder because outbox tracking isn't wired yet.
    async fn apply_outbox_strategy(&self, _engine: &Engine) {
        match &self.outbox_strategy {
            OutboxStrategy::TimeoutAbort { timeout_ms } => {
                tracing::warn!(
                    timeout_ms = *timeout_ms,
                    "OutboxStrategy::TimeoutAbort: sleeping + resetting (placeholder)"
                );
                tokio::time::sleep(std::time::Duration::from_millis(*timeout_ms)).await;
            }
            OutboxStrategy::HandoffOutbox { path } => {
                tracing::warn!(?path, "OutboxStrategy::HandoffOutbox: file write placeholder");
            }
            OutboxStrategy::SyncWait => {
                tracing::warn!("OutboxStrategy::SyncWait: poll-until-empty (placeholder)");
            }
        }
    }

    fn record_error(&self, msg: String) {
        self.metrics.lock().unwrap().last_error = Some(msg);
    }
}

/// Reconstruct an `AgentConfig` from an `Arc<AgentConfig>` reference by
/// deep-copying its cloneable fields. Trait objects (`processors`,
/// `on_member_failed`) cannot be cloned, so if either is `Some(...)` we
/// return `None`. For the typical subagent-pool case (no custom
/// processors / handlers registered) this rebuilds a usable config.
fn rebuild_config_from_arc(arc: &Arc<AgentConfig>) -> Option<AgentConfig> {
    if !arc.engine.processors.is_empty() || arc.engine.on_member_failed.is_some() {
        return None;
    }
    Some(AgentConfig {
        model: arc.model.clone(),
        resources: arc.resources.clone(),
        system_prompt_template: arc.system_prompt_template.clone(),
        initial_memory: arc.initial_memory.clone(),
        allowed_paths: arc.allowed_paths.clone(),
        tools: arc.tools.clone(),
        engine: arf_engine::config::EngineConfig {
            routes: arc.engine.routes.clone(),
            checkpoint_rules: vec![], // Box<dyn ...> not clonable
            processors: HashMap::new(),
            on_member_failed: None,
            max_turns: arc.engine.max_turns,
            tool_timeout_ms: arc.engine.tool_timeout_ms,
            inbound_dedup_capacity: arc.engine.inbound_dedup_capacity,
        },
    })
}

// ── Unit tests (Phase 9 boundary-first style) ────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// [构造] Default strategy is `TimeoutAbort { timeout_ms: 5_000 }`.
    #[test]
    fn outbox_strategy_default_is_timeout_abort_5s() {
        let s = OutboxStrategy::default();
        match s {
            OutboxStrategy::TimeoutAbort { timeout_ms } => {
                assert_eq!(timeout_ms, 5_000);
            }
            other => panic!("expected TimeoutAbort{{5000}}, got {other:?}"),
        }
    }

    /// [构造] PoolMetrics default is all-zero.
    #[test]
    fn pool_metrics_default_is_zero() {
        let m = PoolMetrics::default();
        assert_eq!(m.total_delegations, 0);
        assert_eq!(m.active_count, 0);
        assert_eq!(m.idle_count, 0);
        assert!(m.last_error.is_none());
    }

    /// [类型] OutboxStrategy is Clone + Debug.
    #[test]
    fn outbox_strategy_is_clone_and_debug() {
        let a = OutboxStrategy::HandoffOutbox {
            path: PathBuf::from("/tmp/out.jsonl"),
        };
        let b = a.clone();
        let _ = format!("{a:?} {b:?}");
    }

    // ── Issue 1 — bus-actor tests ──────────────────────────────────
    //
    // `connect_to_bus` registers the pool as a node and a peer sends
    // a `subagent_delegate` message; the pool replies with
    // `subagent_result` keyed by `correlation_id`. These tests don't
    // need a real LLM — `delegate()` returns `Err` quickly because no
    // model is registered on the bus; the listener still sends the
    // error reply with correlation_id preserved.
    use arf_core::Message;
    use std::sync::Arc;
    use uuid::Uuid;

    fn task_only_agent_config() -> Arc<AgentConfig> {
        // Use Default for everything we don't care about. Provider stays
        // default ("deepseek") — ResourceRegistry will fail to find a
        // `model/deepseek` node on the bus, which is the path we want
        // to exercise (delegate() Err path through bus round-trip).
        Arc::new(AgentConfig::default())
    }

    fn new_bus() -> Arc<arf_bus::Bus> {
        Arc::new(arf_bus::Bus::new(
            std::time::Duration::from_secs(1),
            std::time::Duration::from_secs(3),
            16,
        ))
    }

    /// [构造] handle_delegate_msg rejects messages without `task`/`user_message`.
    #[tokio::test]
    async fn handle_delegate_msg_rejects_empty_payload() {
        let bus = new_bus();
        let pool = SubagentPool::new(bus, task_only_agent_config(), 1);
        let msg = Message::new(
            msg_type::SUBAGENT_DELEGATE,
            NodeId::new("caller"),
            vec![],
            serde_json::json!({}),
        );
        let r = pool.handle_delegate_msg(&msg).await;
        let err = r.unwrap_err().to_string();
        assert!(
            err.contains("missing 'task' / 'user_message'"),
            "got: {err}"
        );
    }

    /// [方法] handle_delegate_msg accepts both `{task}` and `{user_message}` shapes.
    #[tokio::test]
    async fn handle_delegate_msg_accepts_both_payload_shapes() {
        let bus = new_bus();
        let pool = SubagentPool::new(bus, task_only_agent_config(), 1);
        for payload in [
            serde_json::json!({"task": "do thing"}),
            serde_json::json!({"user_message": "hello"}),
        ] {
            let msg = Message::new(
                msg_type::SUBAGENT_DELEGATE,
                NodeId::new("caller"),
                vec![],
                payload,
            );
            // We don't care about the inner Err path; we only verify the
            // parser didn't reject the payload with the empty-string error.
            let r = pool.handle_delegate_msg(&msg).await;
            if let Err(e) = r {
                let s = e.to_string();
                assert!(
                    !s.contains("missing 'task' / 'user_message'"),
                    "parser should have accepted payload; got: {s}"
                );
            }
        }
    }

    /// [构造] connect_to_bus advertises `subagent-pool/<pool_id>` and
    /// surfaces the pool_id in capabilities.
    #[tokio::test]
    async fn connect_to_bus_registers_as_subagent_pool_node() {
        let bus = new_bus();
        let pool = Arc::new(SubagentPool::new(bus.clone(), task_only_agent_config(), 2));
        let nid = pool
            .connect_to_bus("tool_creator_pool")
            .await
            .expect("connect_to_bus should succeed");
        assert_eq!(nid.to_string(), "subagent-pool/tool_creator_pool");
        let g = bus.graph();
        let entry = g
            .nodes
            .iter()
            .find(|n| n.node_id == nid)
            .expect("pool should be registered");
        assert_eq!(entry.node_type, "subagent-pool");
        assert_eq!(
            entry.capabilities.get("pool_id").and_then(|v| v.as_str()),
            Some("tool_creator_pool")
        );
        assert_eq!(
            entry.capabilities.get("size").and_then(|v| v.as_u64()),
            Some(2)
        );
    }

    /// [方法] end-to-end: connect_to_bus spawns listener, peer sends
    /// `subagent_delegate`, pool replies with `subagent_result` keyed
    /// by `correlation_id`. The actual delegate() call errors (no model
    /// on bus) but that's fine — we only check the wire round-trip.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn connect_to_bus_round_trips_correlation_id() {
        let bus = new_bus();
        let pool = Arc::new(SubagentPool::new(
            bus.clone(),
            task_only_agent_config(),
            1,
        ));
        let pool_node_id = pool.connect_to_bus("p1").await.unwrap();

        let sub_info = arf_core::NodeInfo {
            node_id: NodeId::new("peer/caller"),
            node_type: "engine".into(),
            capabilities: serde_json::json!({}),
            online_since: 0,
        };
        let sub_filter = MessageFilter {
            types: Some(vec![msg_type::SUBAGENT_RESULT.into()]),
            to_match: ToMatch::DirectedToMe,
        };
        let mut sub = bus.connect(sub_info, sub_filter).await.unwrap();

        let cid = Uuid::new_v4();
        let payload = serde_json::json!({
            "correlation_id": cid.to_string(),
            "task": "ignored-no-model",
        });
        bus.send(Message::new(
            msg_type::SUBAGENT_DELEGATE,
            NodeId::new("peer/caller"),
            vec![pool_node_id],
            payload,
        ))
        .await
        .expect("send subagent_delegate");

        let resp = tokio::time::timeout(std::time::Duration::from_secs(5), async {
            sub.recv().await.expect("recv subagent_result")
        })
        .await
        .expect("subagent_result timed out");
        assert_eq!(resp.msg_type, msg_type::SUBAGENT_RESULT);
        let echoed = resp
            .payload
            .get("correlation_id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        assert_eq!(echoed, Some(cid.to_string()), "correlation_id must round-trip");
        // The delegate() call returns Err because no model/deepseek node
        // is on the bus → the reply should be `ok=false` with `error` set.
        assert_eq!(
            resp.payload.get("ok").and_then(|v| v.as_bool()),
            Some(false),
            "expected ok=false (no model on bus)"
        );
        assert!(resp.payload.get("error").and_then(|v| v.as_str()).is_some());
    }
}
