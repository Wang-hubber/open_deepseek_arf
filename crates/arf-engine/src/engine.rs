//! Engine — ReAct 循环 actor（Phase 6 §3 / §6.4 / §6.5 / §6.6）。

use arf_bus::NodeHandle;
use arf_core::{
    ActionMessage, Checkpoint, Message, MessageIntent, ModelCall, ModelMessage, NodeId,
    NodeInfo, State, ToMatch, ToolCall, ToolExec, WaitStrategy,
};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use crate::checkpoint::{self as cp_eval, DiscoveryCache};
use crate::config::AgentConfig;
use crate::dedup::InboundDedupCache;
use crate::dispatcher::{DispatchDecision, HandlerRegistry};
use crate::error::{BuildError, RunError};
use crate::registry::ResourceRegistry;
use arf_core::msg_type::{MODEL_RESPONSE, PEER_MESSAGE, PEER_REPLY, TOOL_RESULT};
use arf_session::{ModelCallRecord, PendingPeerMessage, SessionError, ToolCallRecord};
use std::sync::Arc;
use tokio::sync::Mutex;

/// ReAct loop actor. Phase 6 §0.1 — "Engine 是 Bus 上的一个 Actor"。
/// 6.3 实现最小骨架；6.4 实现完整 ReAct 主循环；6.5 实现 Checkpoint 评估与 dispatch；
/// 6.6 实现 WaitEvent 队列与 WaitStrategy 触发；
/// 6.7 实现 DiscoveryCache 加速 Capability 解析。
pub struct Engine {
    config: AgentConfig,
    agent_id: NodeId,
    /// Connection to primary Bus.
    handle: NodeHandle,
    /// Primary bus handle — held by Engine so we can query `graph()` for
    /// Checkpoint Rule's Discovery route resolution (Phase 6 task 6.5).
    primary_bus: Arc<arf_bus::Bus>,
    /// Capability → recipients cache. Phase 6 task 6.7.
    discovery_cache: Arc<DiscoveryCache>,
    /// Raw system_prompt_template (no `{{skills}}` substitution).
    system_prompt_template: String,
    /// Session-stable memory items; each pushed as a separate system message.
    initial_memory: Vec<String>,
    /// Declared resource → NodeId mapping. Build-time snapshot, read-only at runtime.
    registry: ResourceRegistry,
    /// Message dispatch registry (Phase 8 task F2).
    handlers: Arc<Mutex<HandlerRegistry>>,
    /// Optional session store (Phase 8 task F5). None = no persistence.
    session_store: Option<Arc<dyn arf_session::SessionStore>>,
    /// Session ID for this engine instance (Phase 8 task F5).
    session_id: String,
    /// Task 19: process-level LRU dedup for inbound reply correlation_ids.
    /// Absorbs self-resend duplicates (sender == receiver) without SQL.
    /// **Process-level only** — cross-restart dedup is application
    /// responsibility (see spec §4.4).
    inbound_dedup: InboundDedupCache,
    /// Team Engine v1.x — Task 3: when true, Engine is a per-task subagent
    /// (Task 4 will use this in `reset_state()` / `run_once()`). Default false.
    ephemeral: bool,
}

impl Engine {
    /// Internal — only `EngineBuilder::build` calls this.
    pub(crate) async fn new(
        buses: Vec<Arc<arf_bus::Bus>>,
        config: AgentConfig,
        registry: ResourceRegistry,
        agent_id_override: Option<NodeId>,
        auto_subscribe: Vec<String>,
        ephemeral: bool,
    ) -> Result<Self, BuildError> {
        let primary = buses[0].clone();
        // Phase 9 F-018: explicit agent_id (from EngineBuilder::with_agent_id)
        // avoids NodeId collision when multiple Engines share a provider.
        let node_id = agent_id_override
            .unwrap_or_else(|| NodeId::new(format!("engine/{}", config.model.provider)));
        let info = NodeInfo {
            node_id: node_id.clone(),
            node_type: "engine".into(),
            capabilities: serde_json::json!({
                "kind": "engine",
                "provider": config.model.provider,
                "model": config.model.model_name,
            }),
            online_since: 0,
        };

        // Build Engine's filter — only response msg_types we care about,
        // plus any F-019 auto-subscribe types the app registered.
        let mut types = engine_response_types(&config);
        for t in auto_subscribe {
            if !types.contains(&t) {
                types.push(t);
            }
        }
        let filter = arf_core::MessageFilter {
            types: if types.is_empty() { None } else { Some(types) },
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };

        let handle = primary
            .connect(info.clone(), filter)
            .await
            .map_err(|e| BuildError::PrimaryBusConnect(e.to_string()))?;

        // 6.7: Spawn lifecycle listener that invalidates the DiscoveryCache
        // when nodes come online or go offline. Phase 9 F-016: also invokes
        // the configured `OnMemberFailedHandler` on offline so apps can react
        // (previously the handler was registered but never called — silent
        // dead code).
        let discovery_cache = Arc::new(DiscoveryCache::new());
        let cache_for_listener = discovery_cache.clone();
        let on_member_failed = config.engine.on_member_failed.clone();
        let agent_id_for_handler = info.node_id.clone();
        let mut lifecycle_rx = primary.subscribe();
        tokio::spawn(async move {
            while let Ok(m) = lifecycle_rx.recv().await {
                if m.msg_type == "node_online" || m.msg_type == "node_offline" {
                    cache_for_listener.invalidate();
                }
                // F-016: invoke the handler on offline. Default = FailSession
                // (matches legacy behaviour). Handler is `Option`, so we skip
                // cleanly when the app didn't supply one.
                if m.msg_type == arf_core::msg_type::NODE_OFFLINE {
                    if let Some(h) = &on_member_failed {
                        let reason = format!(
                            "node_offline from bus (id={})",
                            m.from.as_str()
                        );
                        let _ = h.handle(&agent_id_for_handler, &m.from, &reason);
                    }
                }
            }
        });

        let system_prompt_template = config.system_prompt_template.clone();
        let initial_memory = config.initial_memory.clone();

        // Default session_id is derived from the agent_id; can be overridden
        // via `with_session_id()` on the builder.
        let session_id = info.node_id.to_string();
        let dedup_capacity = config.engine.inbound_dedup_capacity;

        Ok(Self {
            config,
            agent_id: info.node_id,
            handle,
            primary_bus: primary.clone(),
            discovery_cache,
            system_prompt_template,
            initial_memory,
            registry,
            handlers: Arc::new(Mutex::new(HandlerRegistry::new())),
            session_store: None,
            session_id,
            inbound_dedup: InboundDedupCache::new(dedup_capacity),
            ephemeral,
        })
    }

    /// Borrow the DiscoveryCache (test hook). Phase 6 task 6.7.
    pub fn discovery_cache(&self) -> &DiscoveryCache {
        &self.discovery_cache
    }

    /// Borrow the primary Bus Arc (used by Checkpoint evaluation to query
    /// `graph()` for Discovery-route resolution). Phase 6 task 6.5.
    pub fn primary_bus(&self) -> &Arc<arf_bus::Bus> {
        &self.primary_bus
    }

    pub fn config(&self) -> &AgentConfig { &self.config }
    pub fn system_prompt(&self) -> &str { &self.system_prompt_template }
    pub fn agent_id(&self) -> &NodeId { &self.agent_id }
    pub fn handle(&self) -> &NodeHandle { &self.handle }

    /// Borrow the handler registry Arc (Phase 8 task F2).
    pub fn handlers(&self) -> Arc<Mutex<HandlerRegistry>> {
        self.handlers.clone()
    }

    /// Register a message handler. Replaces any prior registration for the
    /// same `msg_type` if `replace=true`; otherwise appends. Phase 8 task F2.
    pub fn add_handler(&mut self, handler: Arc<dyn crate::dispatcher::MessageHandler>, replace: bool) {
        let reg = self.handlers.clone();
        let mut reg = reg.blocking_lock();
        if replace {
            reg.replace(handler.msg_type(), handler);
        } else {
            reg.register(handler);
        }
    }

    /// Borrow the session store (Phase 8 task F5).
    pub fn session_store(&self) -> Option<&Arc<dyn arf_session::SessionStore>> {
        self.session_store.as_ref()
    }

    /// Install a session store and set the session_id. Phase 8 task F5.
    pub(crate) fn install_session_store(
        &mut self,
        store: Arc<dyn arf_session::SessionStore>,
        session_id: String,
    ) {
        self.session_store = Some(store);
        self.session_id = session_id;
    }

    /// Current session id (Phase 8 task F5).
    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    /// Team Engine v1.x — Task 3: true when this Engine was built via
    /// `EngineBuilder::ephemeral(true)`. Read by `reset_state()` /
    /// `run_once()` (Task 4) to behave like a per-task subagent.
    pub fn is_ephemeral(&self) -> bool {
        self.ephemeral
    }

    /// Dispatch one incoming message via the registered handlers. Returns
    /// `Handled` if any handler consumed it; `Deferred` otherwise.
    /// Phase 8 task F2.
    pub fn dispatch_incoming(
        &self,
        msg: Message,
    ) -> Result<crate::dispatcher::HandlerOutcome, RunError> {
        let reg = self.handlers.clone();
        let reg = reg.blocking_lock();
        let ctx = crate::dispatcher::HandlerContext {
            bus: &self.primary_bus,
            engine_id: &self.agent_id,
            session_id: &self.session_id,
            from_bus: msg.from_bus.unwrap_or(arf_core::BusId(uuid::Uuid::nil())),
        };
        reg.dispatch(&ctx, msg)
    }

    /// Phase 8 task F5 / Phase 9 F-012: snapshot current state to the configured
    /// store. Called at each of the 5 Checkpoint positions in run().
    ///
    /// Unlike the earlier best-effort fire-and-forget version, this awaits the
    /// store write and propagates failure: a failed snapshot aborts the current
    /// round (checkpoints are the replay contract — silently continuing would
    /// leave the persisted session incomplete).
    pub async fn snapshot_if_configured(
        &self,
        state: &State,
        checkpoint: Checkpoint,
    ) -> Result<(), RunError> {
        if let Some(store) = &self.session_store {
            let snap = arf_session::CheckpointSnapshot::new(
                checkpoint,
                state.over_view.turn_count,
            );
            store
                .snapshot(&self.session_id, state, &snap)
                .await
                .map_err(|e| RunError::SnapshotFailed {
                    session_id: self.session_id.clone(),
                    reason: e.to_string(),
                })?;
        }
        Ok(())
    }

    /// 完整 ReAct 主循环（6.4）+ 5 Checkpoint 插入（6.5）。
    ///
    /// 1. 推 system prompt + user message
    /// 2. 循环（每 turn）：
    ///    a. Checkpoint::BeforeModelCall 评估 + dispatch（phase6-6.5）
    ///    b. model_call → model_response（content + tool_calls）
    ///    c. Checkpoint::AfterModelCall 评估 + dispatch
    ///    d. 推 assistant message
    ///    e. 若 content 无 tool_calls → Checkpoint::RoundEnd → return content
    ///    f. 否则依次 tool_exec，每个 tool_exec：
    ///       - BeforeToolExec checkpoint
    ///       - tool_exec → tool_result（推 tool message）
    ///       - AfterToolExec checkpoint
    /// 3. 终止条件（任一满足即返）：
    ///    - content 无 tool_calls（纯文本输出）
    ///    - turn_count >= max_turns
    ///    - cancel.cancelled()
    ///    - CheckpointRule 输出未注册 msg_type（programming bug）
    pub async fn run(
        &mut self,
        state: &mut State,
        user_input: String,
        cancel: CancellationToken,
    ) -> Result<String, RunError> {
        // Task 18: record round_start (with the round number we are about to
        // enter — `prepare_round` increments state.over_view.round_count).
        let round_number = state.over_view.round_count as u32 + 1;
        let started_at = chrono::Utc::now();
        self.maybe_record_round_start(round_number).await;

        let result = self.run_inner(state, user_input, cancel).await;

        let duration_ms = (chrono::Utc::now() - started_at).num_milliseconds().max(0) as u64;
        self.maybe_record_round_end(round_number, duration_ms).await;

        result
    }

    /// Inner ReAct loop body extracted from `run()` so the wrapper can
    /// surround it with round_start / round_end events.
    async fn run_inner(
        &mut self,
        state: &mut State,
        user_input: String,
        cancel: CancellationToken,
    ) -> Result<String, RunError> {
        // Phase 9 F-012: fail fast if a session store is configured but the
        // session was never pre-saved — otherwise every checkpoint snapshot
        // would silently fail against a NotFound session.
        if let Some(store) = &self.session_store {
            let exists = store.exists(&self.session_id).await.map_err(|e| {
                RunError::SnapshotFailed {
                    session_id: self.session_id.clone(),
                    reason: e.to_string(),
                }
            })?;
            if !exists {
                return Err(RunError::SessionNotPreSaved {
                    session_id: self.session_id.clone(),
                });
            }
        }

        self.prepare_round(state, &user_input);

        loop {
            if cancel.is_cancelled() {
                return Err(RunError::Stopped);
            }

            // 终止：max_turns（每 turn 后判一次）
            if state.over_view.turn_count as u32 >= self.config.engine.max_turns {
                return Err(RunError::MaxTurnsExceeded {
                    max_turns: self.config.engine.max_turns,
                });
            }

            // ── Checkpoint::BeforeModelCall (6.5) ────────────────────────
            self.evaluate_and_dispatch(state, Checkpoint::BeforeModelCall, cancel.clone())
                .await?;
            if state.over_view.turn_count as u32 >= self.config.engine.max_turns {
                return Err(RunError::MaxTurnsExceeded {
                    max_turns: self.config.engine.max_turns,
                });
            }

            // 1 model turn
            let (content, tool_calls) =
                self.do_model_turn(state, cancel.clone()).await?;

            if cancel.is_cancelled() {
                return Err(RunError::Stopped);
            }

            // ── Checkpoint::AfterModelCall (6.5) ─────────────────────────
            self.evaluate_and_dispatch(state, Checkpoint::AfterModelCall, cancel.clone())
                .await?;

            // 终止：max_turns（model turn 后检）
            if state.over_view.turn_count as u32 >= self.config.engine.max_turns {
                return Err(RunError::MaxTurnsExceeded {
                    max_turns: self.config.engine.max_turns,
                });
            }

            // 终止：纯文本
            if tool_calls.is_empty() {
                // ── Checkpoint::RoundEnd (6.5) ───────────────────────────
                self.evaluate_and_dispatch(state, Checkpoint::RoundEnd, cancel.clone())
                    .await?;
                return Ok(content);
            }

            // tool_exec turns (Phase 8 task F3: concurrent — same model_response
            // returning multiple independent tool_calls now run in parallel).
            //
            // Note: per-tool Checkpoint::Before/AfterToolExec are not fired
            // when running concurrently (would interleave their state mutations).
            // App-level checkpoints fire once before/after the entire batch.
            if cancel.is_cancelled() {
                return Err(RunError::Stopped);
            }
            self.evaluate_and_dispatch(state, Checkpoint::BeforeToolExec, cancel.clone())
                .await?;
            let tool_results = self
                .do_tool_turns_concurrent(state, tool_calls, cancel.clone())
                .await;
            self.evaluate_and_dispatch(state, Checkpoint::AfterToolExec, cancel.clone())
                .await?;

            if let Some(err) = tool_results.into_iter().find_map(|r| r.err()) {
                return Err(err);
            }

            if state.over_view.turn_count as u32 >= self.config.engine.max_turns {
                return Err(RunError::MaxTurnsExceeded {
                    max_turns: self.config.engine.max_turns,
                });
            }
        }
    }

    /// Task 18: best-effort round_start event (no-op if no session_store).
    async fn maybe_record_round_start(&self, round: u32) {
        let Some(store) = self.session_store.as_ref() else {
            return;
        };
        let _ = store.record_round_start(&self.session_id, round).await;
    }

    /// Task 18: best-effort round_end event.
    async fn maybe_record_round_end(&self, round: u32, duration_ms: u64) {
        let Some(store) = self.session_store.as_ref() else {
            return;
        };
        let _ = store
            .record_round_end(&self.session_id, round, duration_ms)
            .await;
    }

    /// Task 18: best-effort model_call_end event. Called by do_model_turn
    /// after parsing the model response.
    async fn maybe_record_model_call(
        &self,
        model: &str,
        usage: &serde_json::Value,
        turn: u32,
        round: u32,
    ) {
        let Some(store) = self.session_store.as_ref() else {
            return;
        };
        // Usage field names: input_tokens / output_tokens / total_tokens
        // (per arf_model_adapter::types::Usage struct).
        let input_tokens = usage.get("input_tokens").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
        let output_tokens = usage.get("output_tokens").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
        let total_tokens = usage.get("total_tokens").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
        let record = ModelCallRecord {
            model: model.to_string(),
            input_tokens,
            output_tokens,
            total_tokens,
            turn,
            round,
            at: chrono::Utc::now(),
        };
        let _ = store
            .record_model_call_end(&self.session_id, &record)
            .await;
    }

    /// Task 18: best-effort tool_call_end event. Called by do_tool_turn
    /// at success + every failure branch (denied, cancelled, error response).
    async fn maybe_record_tool_call(
        &self,
        tool_name: &str,
        duration_ms: u64,
        success: bool,
        error: Option<String>,
        turn: u32,
        round: u32,
    ) {
        let Some(store) = self.session_store.as_ref() else {
            return;
        };
        let record = ToolCallRecord {
            tool_name: tool_name.to_string(),
            duration_ms,
            success,
            error,
            turn,
            round,
            at: chrono::Utc::now(),
        };
        let _ = store
            .record_tool_call_end(&self.session_id, &record)
            .await;
    }

    /// 评估一个 Checkpoint 位置：所有 trigger 匹配的规则；when=true 时 build + 投递。
    ///
    /// - Query intent: publish + register WaitEvent + await response by strategy
    /// - Command intent: publish only
    ///
    /// 投递接收方由 `AgentConfig.routes[msg.msg_type()]` 决定；Strict 取 NodeIds，
    /// Discovery 查当前 bus graph（无缓存；6.7 加 DiscoveryCache）。
    /// Phase 6 task 6.6: 传递 `state` 让 publish_and_await_query 写 WaitEvent。
    async fn evaluate_and_dispatch(
        &mut self,
        state: &mut State,
        trigger: Checkpoint,
        cancel: CancellationToken,
    ) -> Result<(), RunError> {
        if cancel.is_cancelled() {
            return Err(RunError::Stopped);
        }
        // Phase 8 task F5 / Phase 9 F-012: snapshot state at each Checkpoint.
        // A failed snapshot aborts the round (no longer best-effort).
        self.snapshot_if_configured(state, trigger).await?;
        if self.config.engine.checkpoint_rules.is_empty() {
            return Ok(());
        }
        // Build CheckpointMsg list without holding &mut self — keeps borrows disjoint.
        let graph_nodes = self.primary_bus.graph().nodes;
        let rules = &self.config.engine.checkpoint_rules;
        let routes = &self.config.engine.routes;
        let built = cp_eval::evaluate(state, trigger, rules, routes, &graph_nodes, &self.discovery_cache)?;

        for cm in built {
            match cm.msg.intent() {
                MessageIntent::Query => {
                    // 6.6: 默认 All strategy；6.8 暴露 builder 让 App 配置。
                    self.publish_and_await_query(
                        state,
                        cm.msg.as_ref(),
                        cm.recipients,
                        WaitStrategy::All,
                        cancel.clone(),
                    )
                    .await?;
                }
                MessageIntent::Command => {
                    self.publish_only_command(cm.msg.as_ref(), cm.recipients)
                        .await?;
                }
            }
        }
        Ok(())
    }

    /// Query intent 分发：register WaitEvent in State.wait_events + send + await
    /// by strategy. Phase 6 task 6.6.
    ///
    /// **Task 17**: when `msg.msg_type()` is `peer_message`, persist a
    /// `peer_message_sent` event to the configured session store BEFORE
    /// bus.send — so a crash between persist and send is recoverable.
    async fn publish_and_await_query(
        &mut self,
        state: &mut State,
        msg: &dyn ActionMessage,
        recipients: Vec<NodeId>,
        strategy: WaitStrategy,
        cancel: CancellationToken,
    ) -> Result<Vec<Message>, RunError> {
        if cancel.is_cancelled() {
            return Err(RunError::Stopped);
        }
        let cid = msg.correlation_id();
        // Predict response msg_type; fallback to `<msg_type>_result` for App custom types
        // (Phase 6 §1.2 builtin whitelist + convention for App types).
        let response_msg_type = response_msg_type_for(msg.msg_type())
            .unwrap_or_else(|| format!("{}_result", msg.msg_type()));

        let event = arf_core::WaitEvent::new(cid, strategy, recipients.len().max(1));
        let event_id = event.id;
        state.wait_events.push(event);

        // Task 19: record outbound BEFORE bus.send (msg-type-agnostic).
        self.maybe_record_outbound(msg, &recipients).await?;

        let wire = Message::with_from_bus(
            msg.msg_type().to_string(),
            self.agent_id.clone(),
            recipients,
            msg.payload(),
            self.handle.primary_bus_id(),
        );
        if let Err(e) = self.handle.send_message(wire).await {
            state.wait_events.retain(|e| e.id != event_id);
            return Err(RunError::Bus(e));
        }

        // Await responses by strategy; response payloads are ignored for now
        // (6.8 接入 ResponseProcessor 表 by response msg_type).
        let responses = self
            .wait_for_strategy(state, event_id, &[response_msg_type.as_str()], cancel)
            .await?;
        Ok(responses)
    }

    /// Command intent 分发：仅 send（fire-and-forget）。Phase 6 task 6.5.
    ///
    /// **Task 17**: when `msg.msg_type()` is `peer_message`, persist a
    /// `peer_message_sent` event to the configured session store BEFORE
    /// bus.send — so a crash between persist and send is recoverable.
    pub(crate) async fn publish_only_command(
        &self,
        msg: &dyn ActionMessage,
        recipients: Vec<NodeId>,
    ) -> Result<(), RunError> {
        // Task 19: record outbound BEFORE bus.send (msg-type-agnostic).
        self.maybe_record_outbound(msg, &recipients).await?;
        let wire = Message::with_from_bus(
            msg.msg_type().to_string(),
            self.agent_id.clone(),
            recipients,
            msg.payload(),
            self.handle.primary_bus_id(),
        );
        self.handle.send_message(wire).await?;
        Ok(())
    }

    /// Task 19: if a session store is configured, record any async outbound
    /// message (peer_message, HumanHandoff, future) via `record_event` BEFORE
    /// `bus.send` — so a crash between persist and send is recoverable.
    ///
    /// msg-type-agnostic: unlike Task 17's `maybe_record_peer_send` which
    /// only recorded peer_message, this now writes `Event::OutboundSent`
    /// for any async outbound message. The msg_type field distinguishes
    /// the kind (peer_message, human_handoff, …) at the store layer.
    async fn maybe_record_outbound(
        &self,
        msg: &dyn ActionMessage,
        recipients: &[NodeId],
    ) -> Result<(), RunError> {
        let Some(store) = self.session_store.as_ref() else {
            return Ok(());
        };
        let target: Vec<String> = recipients.iter().map(|n| n.to_string()).collect();
        let event = arf_session::Event::OutboundSent {
            msg_type: msg.msg_type().to_string(),
            correlation_id: msg.correlation_id(),
            attempt: 1,
            target,
            payload: msg.payload(),
            captured_at: chrono::Utc::now(),
        };
        store
            .record_event(&self.session_id, &event)
            .await
            .map_err(|e| RunError::SnapshotFailed {
                session_id: self.session_id.clone(),
                reason: format!("record_event: {e}"),
            })?;
        Ok(())
    }

    //// Public API: send a `human_handoff` message to the UI node and await its
    /// reply. Records the outbound event via `maybe_record_outbound` before
    /// sending (Task 19 unified outbox). The UI node id defaults to `"ui"`.
    ///
    /// Returns the `HumanHandoffReply` from the UI, or `RunError` on
    /// timeout / send failure.
    pub async fn handoff_to_human(
        &mut self,
        state: &mut State,
        question: impl Into<String>,
        context: serde_json::Value,
        options: Vec<String>,
        timeout: std::time::Duration,
    ) -> Result<arf_core::HumanHandoffReply, RunError> {
        use arf_core::HumanHandoff;

        let cid = Uuid::new_v4();
        let handoff = HumanHandoff {
            correlation_id: cid,
            question: question.into(),
            context,
            options,
        };
        let recipients = vec![NodeId::new("ui")];

        // 1. record before send (Task 19 unified outbox)
        self.maybe_record_outbound(&handoff, &recipients).await?;

        // 2. send via bus
        let payload = serde_json::to_value(&handoff).map_err(|e| RunError::SnapshotFailed {
            session_id: self.session_id.clone(),
            reason: format!("serialize handoff: {e}"),
        })?;
        let wire = Message::new(
            String::from("human_handoff"),
            self.agent_id.clone(),
            recipients,
            payload,
        );
        if let Err(e) = self.handle.send_message(wire).await {
            return Err(RunError::Bus(e));
        }

        // 3. register WaitEvent + await reply
        let event = arf_core::WaitEvent::new(cid, arf_core::WaitStrategy::All, 1);
        let event_id = event.id;
        state.wait_events.push(event);

        // Set up timeout cancel
        let cancel = tokio_util::sync::CancellationToken::new();
        let cancel_for_timeout = cancel.clone();
        tokio::spawn(async move {
            tokio::time::sleep(timeout).await;
            cancel_for_timeout.cancel();
        });

        let responses = self
            .wait_for_strategy(
                state,
                event_id,
                &["human_handoff_reply"],
                cancel,
            )
            .await?;

        // Decode first reply
        let reply = responses.into_iter().next().ok_or_else(|| {
            RunError::SnapshotFailed {
                session_id: self.session_id.clone(),
                reason: "no human_handoff_reply received".into(),
            }
        })?;
        serde_json::from_value::<arf_core::HumanHandoffReply>(reply.payload).map_err(|e| {
            RunError::SnapshotFailed {
                session_id: self.session_id.clone(),
                reason: format!("decode reply: {e}"),
            }
        })
    }

    /// Task 19: process-level dedup + record InboundReply event for reply-type
    /// messages (peer_reply, human_handoff_reply). Returns Drop if the
    /// correlation_id was already in the LRU cache (self-resend duplicate);
    /// caller should skip handler dispatch.
    ///
    /// Caller is responsible for filtering by msg_type — only invoke for
    /// "peer_reply" or "human_handoff_reply". Defensive: returns Pass for
    /// messages without a correlation_id (treats them as non-replies).
    pub(crate) async fn maybe_record_inbound_reply(
        &self,
        msg: &Message,
    ) -> DispatchDecision {
        let cid = match msg.correlation_id() {
            Some(c) => c,
            None => return DispatchDecision::Pass,  // non-reply; pass through
        };

        // 1. Process-level LRU dedup (fast path; sync).
        if self.inbound_dedup.check_and_record(&cid) {
            log::debug!(
                "dropping duplicate inbound reply cid={cid} msg_type={}",
                msg.msg_type
            );
            return DispatchDecision::Drop;
        }

        // 2. Record InboundReply event (best-effort; do not block dispatch).
        if let Some(store) = self.session_store.as_ref() {
            let event = arf_session::Event::InboundReply {
                msg_type: msg.msg_type.clone(),
                correlation_id: cid,
                source: msg.from.to_string(),
                payload: msg.payload.clone(),
                captured_at: chrono::Utc::now(),
            };
            if let Err(e) = store.record_event(&self.session_id, &event).await {
                log::warn!("failed to record inbound reply event: {e}");
            }
        }

        DispatchDecision::Pass
    }

    /// Task 17: called by `wait_for_strategy` when a `peer_reply` arrives
    /// whose correlation_id matches the in-flight WaitEvent. Best-effort —
    /// a failed write means the next restart may resend, but receiver LRU
    /// dedup absorbs the duplicate.
    async fn record_peer_reply_event(
        &self,
        correlation_id: Uuid,
        source: &NodeId,
    ) -> Result<(), SessionError> {
        let Some(store) = self.session_store.as_ref() else {
            return Ok(());
        };
        store
            .record_peer_reply_received(&self.session_id, correlation_id, &source.to_string())
            .await
    }

    /// 推 user message；inc_round。
    /// system prefix 由 do_model_turn 在每轮拼装（template + memory + skills），
    /// state.messages 保持只存对话。
    pub(crate) fn prepare_round(&self, state: &mut State, user_input: &str) {
        state.push_message(ModelMessage::new("user", user_input));
        state.over_view.last_user_message = user_input.to_string();
        state.inc_round();
    }

    /// Model call turn: send + await + parse + append assistant.
    /// Each model_call is +1 to turn_count（已 send 即计）。
    async fn do_model_turn(
        &mut self,
        state: &mut State,
        cancel: CancellationToken,
    ) -> Result<(String, Vec<ToolCall>), RunError> {
        state.inc_turn();

        // Layered prefix assembly (Phase 6 design spec):
        //   [0]   system: system_prompt_template  (raw, no {{skills}})
        //   [1..N] system: initial_memory[i]      (each entry as own message)
        //   [N+1] system: skills (live BusGraph; cached by hash)
        //   [N+2..] state.messages  (conversation)
        let skills_text = self.registry.skills_text(&self.primary_bus);
        let mut messages: Vec<ModelMessage> = Vec::with_capacity(
            2 + self.initial_memory.len() + state.messages.len(),
        );
        messages.push(ModelMessage::new("system", &self.system_prompt_template));
        for m in &self.initial_memory {
            messages.push(ModelMessage::new("system", m));
        }
        if !skills_text.is_empty() {
            messages.push(ModelMessage::new("system", &skills_text));
        }
        messages.extend(state.messages.iter().cloned());

        let tools = self.registry.tools_for_model(&self.primary_bus);
        // Phase 9 F-005: propagate ModelDecl inference params to the wire so the
        // model adapter can honour them. Previously ModelCall had no
        // model_params and the adapter always received defaults.
        let model_params = arf_core::CoreModelParams {
            thinking_enabled: self.config.model.thinking_enabled,
            temperature: self.config.model.temperature,
            max_tokens: self.config.model.max_output_tokens,
            extra: self.config.model.extra.clone(),
        };
        let model_call = ModelCall::new(messages)
            .with_tools(tools)
            .with_model_params(model_params);
        let cid = model_call.correlation_id;
        let target = self.registry.model_target();
        let msg = Message::with_from_bus(
            model_call.msg_type(),
            self.agent_id.clone(),
            vec![target],
            model_call.payload(),
            self.handle.primary_bus_id(),
        );

        let response = self.send_and_await(state, cid, msg, cancel).await?;

        // Parse ModelResponsePayload nested format (6.20 修复)
        let content = response
            .payload
            .get("message")
            .and_then(|m| m.get("content"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        // tool_calls lives at the TOP level of the wire payload (next to `message`),
        // not nested inside `message`. ModelResponsePayload's serialized form is:
        //   {"message": {...}, "tool_calls": [...], "finish_reason": "...", ...}
        let tool_calls: Vec<ToolCall> = response
            .payload
            .get("tool_calls")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|tc| serde_json::from_value(tc.clone()).ok())
                    .collect()
            })
            .unwrap_or_default();

        // Update context_tokens (usage is at top level per ModelResponsePayload struct)
        if let Some(usage) = response.payload.get("usage") {
            if let Some(tokens) = usage.get("prompt_tokens").and_then(|v| v.as_u64()) {
                state.set_context_tokens(tokens as usize);
            }
            // Task 18: record model_call_end event.
            let model_name = response
                .payload
                .get("model")
                .and_then(|v| v.as_str())
                .unwrap_or(&self.config.model.model_name)
                .to_string();
            self.maybe_record_model_call(
                &model_name,
                usage,
                state.over_view.turn_count as u32,
                state.over_view.round_count as u32,
            )
            .await;
        }

        // Push assistant message（含 tool_calls）；不再 inc_turn（消息是响应非请求）
        let mut assistant_msg = ModelMessage::new("assistant", &content);
        assistant_msg.tool_calls = tool_calls.clone();
        state.push_message(assistant_msg);

        Ok((content, tool_calls))
    }

    /// Run multiple tool calls sequentially but with shared batch context
    /// (Phase 8 task F3 first cut). True concurrent execution requires
    /// splitting `state` into per-call slices, which is a later optimization.
    /// The public API (`do_tool_turns_concurrent`) is what Engine.run() calls.
    /// Errors are aggregated; first error returned.
    async fn do_tool_turns_concurrent(
        &mut self,
        state: &mut State,
        tool_calls: Vec<ToolCall>,
        cancel: CancellationToken,
    ) -> Vec<Result<serde_json::Value, RunError>> {
        let mut results = Vec::with_capacity(tool_calls.len());
        for tc in tool_calls {
            if cancel.is_cancelled() {
                // R7-L1 fix: push tool role sentinel for every cancelled tool
                // so state.messages has assistant + tool (cancelled) pairs.
                // Model on resume sees both messages — no dangling tool_call_id.
                let cancel_content = format!("[cancelled by user] {}", tc.name);
                let mut tool_msg = ModelMessage::new("tool", &cancel_content);
                tool_msg.tool_call_id = Some(tc.id.clone());
                tool_msg.name = Some(tc.name.clone());
                state.push_message(tool_msg);
                results.push(Err(RunError::Stopped));
                continue;
            }
            let r = self.do_tool_turn(state, tc, cancel.clone()).await;
            results.push(r);
        }
        results
    }

    /// Look up the configured permission for a tool by name (Phase 9 F-017).
    /// Defaults to `Allow` when the tool isn't in the configured list, matching
    /// legacy behaviour where all tools were unconditionally allowed.
    pub fn lookup_tool_permission(&self, tool_name: &str) -> arf_core::ToolPermission {
        self.config
            .tools
            .iter()
            .find(|s| s.name == tool_name)
            .map(|s| s.permission.clone())
            .unwrap_or(arf_core::ToolPermission::Allow)
    }

    /// Tool call turn: send + await tool_result + parse + append tool message.
    /// Each tool_exec is +1 to turn_count（已 send 即计）.
    async fn do_tool_turn(
        &mut self,
        state: &mut State,
        tc: ToolCall,
        cancel: CancellationToken,
    ) -> Result<serde_json::Value, RunError> {
        state.inc_turn();

        // Phase 9 F-017: enforce ToolPermission gating before tool_exec.
        let tool_started = std::time::Instant::now();
        let turn = state.over_view.turn_count as u32;
        let round = state.over_view.round_count as u32;

        let permission = self.lookup_tool_permission(&tc.name);
        match permission {
            arf_core::ToolPermission::Deny => {
                let deny_content = format!("tool call denied by policy: {}", tc.name);
                let mut tool_msg = ModelMessage::new("tool", &deny_content);
                tool_msg.tool_call_id = Some(tc.id.clone());
                tool_msg.name = Some(tc.name.clone());
                state.push_message(tool_msg);
                self.maybe_record_tool_call(
                    &tc.name,
                    tool_started.elapsed().as_millis() as u64,
                    false,
                    Some("denied by policy".into()),
                    turn,
                    round,
                )
                .await;
                return Ok(serde_json::json!({
                    "content": "",
                    "error": deny_content,
                    "permission": "deny",
                }));
            }
            arf_core::ToolPermission::Ask => {
                // Send permission_request, await permission_response.
                let granted = self
                    .request_permission(state, &tc, cancel.clone())
                    .await?;
                if !granted {
                    let denied_content =
                        format!("user denied tool call: {}", tc.name);
                    let mut tool_msg = ModelMessage::new("tool", &denied_content);
                    tool_msg.tool_call_id = Some(tc.id.clone());
                    tool_msg.name = Some(tc.name.clone());
                    state.push_message(tool_msg);
                    self.maybe_record_tool_call(
                        &tc.name,
                        tool_started.elapsed().as_millis() as u64,
                        false,
                        Some("user denied".into()),
                        turn,
                        round,
                    )
                    .await;
                    return Ok(serde_json::json!({
                        "content": "",
                        "error": denied_content,
                        "permission": "deny",
                    }));
                }
                // User approved → fall through to normal tool_exec path.
            }
            arf_core::ToolPermission::Allow => { /* proceed normally */ }
        }

        // Engine routes tool_exec directly to the MCP node that owns
        // this tool. Precedence: (1) model's explicit `tc.target` if
        // provided, (2) BusGraph owner lookup, (3) broadcast as last
        // resort (legacy). model_call / tool_exec are built-in actions —
        // routing is Engine's job, no user config required.
        let target = tc
            .target
            .clone()
            .or_else(|| self.registry.owner_of_tool(&tc.name));
        let tool_exec = ToolExec {
            correlation_id: Uuid::new_v4(),
            tool_name: tc.name.clone(),
            arguments: tc.arguments.clone(),
            target: target.clone(),
        };
        let cid = tool_exec.correlation_id;
        let to: Vec<NodeId> = match target {
            Some(t) => vec![t],
            None => Vec::new(),
        };
        let msg = Message::with_from_bus(
            tool_exec.msg_type(),
            self.agent_id.clone(),
            to,
            tool_exec.payload(),
            self.handle.primary_bus_id(),
        );

        let response = match self.send_and_await(state, cid, msg, cancel.clone()).await {
            Ok(r) => r,
            Err(e @ RunError::Stopped) => {
                // R7-L1 fix: push tool role sentinel so model sees both halves
                // of the assistant+tool message pair (no dangling tool_call_id).
                let cancel_content = format!("[cancelled mid-execution] {}", tc.name);
                let mut tool_msg = ModelMessage::new("tool", &cancel_content);
                tool_msg.tool_call_id = Some(tc.id.clone());
                tool_msg.name = Some(tc.name.clone());
                state.push_message(tool_msg);
                self.maybe_record_tool_call(
                    &tc.name,
                    tool_started.elapsed().as_millis() as u64,
                    false,
                    Some("cancelled".into()),
                    turn,
                    round,
                )
                .await;
                return Err(e);
            }
            Err(e) => {
                self.maybe_record_tool_call(
                    &tc.name,
                    tool_started.elapsed().as_millis() as u64,
                    false,
                    Some(e.to_string()),
                    turn,
                    round,
                )
                .await;
                return Err(e);
            }
        };

        // Parse tool result
        let result_content = response
            .payload
            .get("content")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let error_str = response
            .payload
            .get("error")
            .and_then(|v| v.as_str())
            .map(String::from);

        // Push tool message；不再 inc_turn（消息是响应非请求）
        let content = match error_str {
            Some(e) => format!("error: {e}"),
            None => result_content,
        };
        let mut tool_msg = ModelMessage::new("tool", &content);
        tool_msg.tool_call_id = Some(tc.id.clone());
        tool_msg.name = Some(tc.name.clone());
        state.push_message(tool_msg);

        // Task 18: record tool_call_end (success or error-result).
        let success = response.payload.get("error").is_none();
        self.maybe_record_tool_call(
            &tc.name,
            tool_started.elapsed().as_millis() as u64,
            success,
            if success { None } else { response.payload.get("error").and_then(|v| v.as_str()).map(String::from) },
            turn,
            round,
        )
        .await;

        Ok(response.payload)
    }

    /// Send a permission_request to bus and wait for permission_response.
    /// Returns Ok(true) if user approved, Ok(false) if denied or timed out.
    /// (Phase 9 F-017.)
    async fn request_permission(
        &mut self,
        state: &mut State,
        tc: &ToolCall,
        cancel: CancellationToken,
    ) -> Result<bool, RunError> {
        let cid = Uuid::new_v4();
        let payload = serde_json::json!({
            "correlation_id": cid.to_string(),
            "tool_name": tc.name,
            "arguments": tc.arguments,
            "tool_call_id": tc.id,
        });
        // Broadcast the request; any responder (frontend / human handoff node)
        // that subscribes to `permission_request` will reply.
        let msg = Message::new_broadcast(
            arf_core::msg_type::PERMISSION_REQUEST,
            self.agent_id.clone(),
            payload,
        );
        // Manually send + wait (we need to control expected_types).
        let event = arf_core::WaitEvent::new(cid, WaitStrategy::All, 1);
        let event_id = event.id;
        state.wait_events.push(event);
        if let Err(e) = self.handle.send_message(msg).await {
            state.wait_events.retain(|e| e.id != event_id);
            return Err(RunError::Bus(e));
        }
        let mut responses = self
            .wait_for_strategy(
                state,
                event_id,
                &[arf_core::msg_type::PERMISSION_RESPONSE],
                cancel,
            )
            .await?;
        let resp = responses.remove(0);
        let allow = resp
            .payload
            .get("allow")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        Ok(allow)
    }

    /// Send a pre-constructed message + register WaitEvent + await first response.
    ///
    /// Used for Engine's own ModelCall / ToolExec (built-in msg types). The
    /// `to` field on the message determines `expected` receivers (0 means
    /// broadcast — typically 1 expected unless route is Discovery multi-receiver).
    /// Phase 6 task 6.6.
    async fn send_and_await(
        &mut self,
        state: &mut State,
        cid: Uuid,
        msg: Message,
        cancel: CancellationToken,
    ) -> Result<Message, RunError> {
        if cancel.is_cancelled() {
            return Err(RunError::Stopped);
        }
        let response_msg_type = response_msg_type_for(&msg.msg_type)
            .unwrap_or_else(|| format!("{}_result", msg.msg_type));

        // Strict from msg.to; broadcast (to=[]) treated as expected=1 (single responder).
        let expected = if msg.to.is_empty() { 1 } else { msg.to.len() };
        let event = arf_core::WaitEvent::new(cid, WaitStrategy::All, expected);
        let event_id = event.id;
        state.wait_events.push(event);

        if let Err(e) = self.handle.send_message(msg).await {
            state.wait_events.retain(|e| e.id != event_id);
            return Err(RunError::Bus(e));
        }

        // Engine-built-in ModelCall/ToolExec always expect model_response/tool_result.
        let expected_types: &[&str] = match response_msg_type.as_str() {
            MODEL_RESPONSE => &[MODEL_RESPONSE],
            TOOL_RESULT => &[TOOL_RESULT],
            other => &[other],
        };
        let mut responses = self
            .wait_for_strategy(state, event_id, expected_types, cancel)
            .await?;
        // For All strategy with multiple receivers, just return the first.
        Ok(responses.remove(0))
    }

    /// Loop on handle.recv; accumulate responses matching the WaitEvent's cid
    /// until the configured strategy triggers.
    ///
    /// - All: trigger when received.len() == event.expected
    /// - Any: trigger on first response (rest are received but may be discarded by caller)
    /// - Count(n): trigger when received.len() >= n
    ///
    /// Cancel: retain-removes the event; returns Err(Stopped).
    /// Phase 6 task 6.6.
    async fn wait_for_strategy(
        &mut self,
        state: &mut State,
        event_id: Uuid,
        expected_response_types: &[&str],
        cancel: CancellationToken,
    ) -> Result<Vec<Message>, RunError> {
        let mut received = Vec::new();
        loop {
            // Race recv against cancel so cancellation fires immediately
            // (handle.recv() alone can block indefinitely if no responder).
            let msg = tokio::select! {
                biased;
                _ = cancel.cancelled() => {
                    state.wait_events.retain(|e| e.id != event_id);
                    return Err(RunError::Stopped);
                }
                res = self.handle.recv() => res.map_err(|_| RunError::Internal("handle closed".into()))?,
            };

            // Look up event (may have been removed by another path).
            let event_info = state
                .wait_events
                .iter()
                .find(|e| e.id == event_id)
                .map(|e| (e.correlation_id, e.strategy, e.expected));
            let (our_cid, strategy, expected) = match event_info {
                Some(x) => x,
                None => continue, // event gone — discard msg
            };

            // Filter: msg_type in expected_response_types AND payload.correlation_id matches.
            if !expected_response_types.contains(&msg.msg_type.as_str()) {
                // Task 19: for reply-type messages (peer_reply, human_handoff_reply),
                // run the LRU dedup check + InboundReply event recording first.
                // If Drop is returned, the message is a self-resend duplicate and
                // we skip handler dispatch entirely.
                let is_reply = matches!(
                    msg.msg_type.as_str(),
                    arf_core::msg_type::PEER_REPLY | "human_handoff_reply"
                );
                if is_reply {
                    match self.maybe_record_inbound_reply(&msg).await {
                        DispatchDecision::Drop => continue,
                        DispatchDecision::Pass => {}
                    }
                }
                // F-024 fix: before dropping, dispatch to registered
                // MessageHandlers (peer_message, peer_reply, custom app
                // types). Handlers return Handled to consume the message,
                // Deferred to drop it. Engine's main wait loop is not
                // blocked — handlers must be quick or spawn their own task.
                let outcome = self.dispatch_incoming(msg.clone());
                let _ = outcome; // Handled/Deferred both fine here
                continue;
            }
            // A4-001: typed accessor (centralises the Uuid↔string conversion).
            let msg_cid = msg.correlation_id();
            if msg_cid != Some(our_cid) {
                continue;
            }

            // Phase 6 task 6.8 + F-025: dispatch to ResponseProcessor if
            // registered. F-025 fix: surface processor errors as `RunError::Processor`
            // (was: silently swallowed via `let _ = ...`). The engine aborts
            // the current round on processor error so app-level bugs aren't
            // hidden — app can catch `RunError::Processor` and decide whether
            // to retry, log, or treat as fatal.
            if let Some(processor) = self.config.engine.processors.get(msg.msg_type.as_str()) {
                if processor.handles(&msg.msg_type) {
                    if let Err(e) = processor.process(&msg) {
                        return Err(RunError::Processor {
                            msg_type: msg.msg_type.clone(),
                            reason: e.to_string(),
                        });
                    }
                }
            }

            // Task 17: persist peer_reply_received event (best-effort).
            if msg.msg_type == PEER_REPLY {
                if let Some(cid) = msg_cid {
                    let _ = self.record_peer_reply_event(cid, &msg.from).await;
                }
            }

            received.push(msg);

            let triggered = match strategy {
                WaitStrategy::All => received.len() >= expected,
                WaitStrategy::Any => true,
                WaitStrategy::Count(n) => received.len() >= n as usize,
            };

            if triggered {
                state.wait_events.retain(|e| e.id != event_id);
                return Ok(received);
            }
        }
    }

    /// Team Engine v1.x — Task 4: clear conversation history for reuse.
    ///
    /// Engine + State split (2026-07-05): `State` is caller-owned, so this
    /// takes `&mut State` explicitly. `&self` is enough because we only read
    /// `self.ephemeral` and the placeholder `self.collect_outbox_pending()`.
    ///
    /// Outbox tracking is a placeholder (Task 5 will wire `SubagentPool` /
    /// `OutboxStrategy`); today it always returns `[]`, so reset always
    /// succeeds on a fresh ephemeral engine.
    pub fn reset_state(&self, state: &mut State) -> Result<(), EngineError> {
        let pending = self.collect_outbox_pending();
        if !pending.is_empty() {
            return Err(EngineError::OutboxNotEmpty { pending });
        }
        // Clear conversation history (system prefix is rebuilt each turn from
        // template + initial_memory + skills — it lives on Engine, not in
        // state.messages).
        state.messages.clear();
        // Reset ReAct counters so the next run_once starts fresh.
        state.over_view.turn_count = 0;
        state.over_view.round_count = 0;
        state.over_view.last_user_message.clear();
        state.wait_events.clear();
        Ok(())
    }

    /// Placeholder for outbox tracking — returns `[]` until Task 5 wires up
    /// `SubagentPool` / `OutboxStrategy`. Kept as a method so future
    /// implementations can swap in JSONL scanning without changing callers.
    fn collect_outbox_pending(&self) -> Vec<String> {
        Vec::new()
    }

    /// Task 19: resend any outbound event that was sent but for which no
    /// matching InboundReply was recorded before the previous Engine instance
    /// died. msg-type-agnostic — covers peer_message, HumanHandoff, future.
    ///
    /// Behavior:
    /// 1. `store.pending_outbound()` derives the outbox (sent - replied).
    /// 2. For each pending entry, reconstruct the wire Message via
    ///    `message_reconstruct::reconstruct_message`, re-`bus.send`, and
    ///    write a fresh `Event::OutboundSent` with `attempt += 1`.
    ///
    /// Receivers dedup via process-level LRU (Task 19 InboundDedupCache).
    /// Cross-restart dedup is application responsibility.
    /// On `bus.send` failure we log and continue (best-effort: the next
    /// restart gets another shot).
    pub async fn resend_pending_outbound(&self) -> Result<usize, RunError> {
        use crate::message_reconstruct::reconstruct_message;

        let Some(store) = self.session_store.as_ref() else {
            return Ok(0);
        };
        let pending = store
            .pending_outbound(&self.session_id)
            .await
            .map_err(|e| RunError::SnapshotFailed {
                session_id: self.session_id.clone(),
                reason: format!("pending_outbound: {e}"),
            })?;
        let count = pending.len();
        for p in pending {
            let attempt = p.attempt + 1;
            let wire = match reconstruct_message(&p, self.agent_id.clone()) {
                Ok(m) => m,
                Err(e) => {
                    log::error!(
                        "resend: failed to reconstruct msg_type={} cid={}: {e}",
                        p.msg_type, p.correlation_id
                    );
                    continue;
                }
            };
            if let Err(e) = self.handle.send_message(wire).await {
                log::error!(
                    "resend: bus.send failed msg_type={} cid={} attempt={}: {e}",
                    p.msg_type, p.correlation_id, attempt
                );
                continue;
            }
            // Persist the new attempt so a future restart doesn't re-send
            // (MAX(attempt) in pending_outbound).
            let event = arf_session::Event::OutboundSent {
                msg_type: p.msg_type.clone(),
                correlation_id: p.correlation_id,
                attempt,
                target: p.target_nodes.clone(),
                payload: p.payload.clone(),
                captured_at: chrono::Utc::now(),
            };
            let _ = store.record_event(&self.session_id, &event).await;
        }
        Ok(count)
    }

    /// Task 17: public test hook — drive `publish_only_command` from a
    /// test without needing a full CheckpointRule setup. Used by e2e tests
    /// to validate the peer_message_sent recording hook in isolation.
    pub async fn test_record_peer_send_via_publish(
        &self,
        msg: &dyn ActionMessage,
        recipients: &[NodeId],
    ) -> Result<bool, RunError> {
        self.publish_only_command(msg, recipients.to_vec()).await?;
        Ok(true)
    }

    /// Task 17: public test hook — exercise the reply recording path used
    /// by `wait_for_strategy` without needing a full in-flight WaitEvent.
    pub async fn test_record_peer_reply(
        &self,
        correlation_id: Uuid,
        source: &str,
    ) -> Result<(), SessionError> {
        self.record_peer_reply_event(correlation_id, &NodeId::new(source))
            .await
    }

    /// Team Engine v1.x — Task 4: run one ReAct round against caller-owned state.
    ///
    /// Adaptation (Engine + State split, 2026-07-05): borrows `state` and
    /// `cancel` from caller. Does NOT auto-reset state after completion —
    /// caller decides when to call `reset_state`. This gives the caller
    /// control over per-task persistence (e.g., inspect logs before reset).
    ///
    /// Implementation: delegates to the existing `Engine::run` ReAct loop.
    /// `run` returns when content has no tool_calls (text-only), or on
    /// max_turns / cancellation — any of these terminates `run_once`.
    /// `turns_consumed` is reported as `state.over_view.turn_count` after
    /// the round (an estimate; may equal 0 if round was rejected at
    /// checkpoint before any model call).
    ///
    /// Takes `&mut self` (not `&self`) because the underlying `run` loop
    /// mutates Engine state (e.g., `wait_events` lifecycle, handler
    /// dispatch). Task 4 deviation from `&self` ideal — noted in report.
    pub async fn run_once(
        &mut self,
        state: &mut State,
        task_input: TaskInput,
        cancel: CancellationToken,
    ) -> Result<TaskResult, RunError> {
        let turns_before = state.over_view.turn_count;
        let content = self
            .run(state, task_input.user_message, cancel)
            .await?;
        let turns_consumed = (state.over_view.turn_count - turns_before) as u32;
        Ok(TaskResult {
            output: serde_json::Value::String(content),
            turns_consumed,
            pending_peer_messages: Vec::new(),
        })
    }
}

/// Team Engine v1.x — Task 4: input for one ephemeral ReAct round.
#[derive(Debug, Clone)]
pub struct TaskInput {
    pub user_message: String,
}

/// Team Engine v1.x — Task 4: output of one ephemeral ReAct round.
///
/// `pending_peer_messages` is reserved for Task 5 (peer messages received
/// during the round that the caller hasn't yet ack'd). Currently always `[]`.
#[derive(Debug, Clone, serde::Serialize)]
pub struct TaskResult {
    pub output: serde_json::Value,
    pub turns_consumed: u32,
    pub pending_peer_messages: Vec<String>,
}

/// Team Engine v1.x — Task 4: engine-level errors (separate from `RunError`,
/// which is for run-loop failures). `OutboxNotEmpty` guards `reset_state`:
/// refuse to clear history while outbox messages are still pending (would
/// leave the caller with no way to retry sending).
#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    #[error("outbox not empty: {pending:?}")]
    OutboxNotEmpty { pending: Vec<String> },
}

/// Engine wants the response msg_types (反向映射 request → response）。
/// Phase 6 §1.2 白名单：model_response / tool_result / memory_op_result。
/// App 自定义类型按惯例响应 `<msg_type>_result`。
///
/// **model_response 和 tool_result 必须始终包含在内**——Engine 的 ReAct 主循环
/// 内部使用 ModelCall/ToolExec（不一定通过 AgentConfig.routes），所以 filter 始终
/// 监听这两个内置响应类型。
fn engine_response_types(config: &AgentConfig) -> Vec<String> {
    let mut types: Vec<String> = Vec::new();
    // Built-in response types always present (Engine's ReAct loop).
    types.push(MODEL_RESPONSE.to_string());
    types.push(TOOL_RESULT.to_string());
    // Routes-derived response types (App-level CheckpointRule dispatch).
    for msg_type in config.engine.routes.keys() {
        if let Some(t) = response_msg_type_for(msg_type) {
            if t != MODEL_RESPONSE && t != TOOL_RESULT {
                types.push(t);
            }
        } else {
            // Convention: App types respond via `<msg_type>_result`
            types.push(format!("{msg_type}_result"));
        }
    }
    types.sort();
    types.dedup();
    types
}

fn response_msg_type_for(request: &str) -> Option<String> {
    match request {
        "model_call" => Some("model_response".into()),
        "tool_exec" => Some("tool_result".into()),
        "subagent_delegate" => Some("subagent_result".into()),
        "peer_message" => Some("peer_reply".into()),
        "memory_op" => Some("memory_op_result".into()),
        "human_handoff" => Some("human_handoff_reply".into()),
        _ => None,
    }
}

