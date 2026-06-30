//! Engine — ReAct 循环 actor（Phase 6 §3 / §6.4 / §6.5 / §6.6）。

use std::sync::Arc;

use arf_bus::NodeHandle;
use arf_core::{
    ActionMessage, Checkpoint, Message, MessageIntent, ModelCall, ModelMessage, NodeId,
    NodeInfo, State, ToMatch, ToolCall, ToolExec, WaitStrategy,
};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use crate::checkpoint as cp_eval;
use crate::config::AgentConfig;
use crate::error::{BuildError, RunError};

const MODEL_RESPONSE: &str = "model_response";
const TOOL_RESULT: &str = "tool_result";

/// ReAct loop actor. Phase 6 §0.1 — "Engine 是 Bus 上的一个 Actor"。
/// 6.3 实现最小骨架；6.4 实现完整 ReAct 主循环；6.5 实现 Checkpoint 评估与 dispatch；
/// 6.6 实现 WaitEvent 队列与 WaitStrategy 触发。
pub struct Engine {
    config: AgentConfig,
    agent_id: NodeId,
    /// Connection to primary Bus.
    handle: NodeHandle,
    /// Primary bus handle — held by Engine so we can query `graph()` for
    /// Checkpoint Rule's Discovery route resolution (Phase 6 task 6.5).
    primary_bus: Arc<arf_bus::Bus>,
    /// Pre-computed system prompt (with {{skills}} substituted at build time)
    system_prompt: String,
}

impl Engine {
    /// Internal — only `EngineBuilder::build` calls this.
    pub(crate) async fn new(
        buses: Vec<Arc<arf_bus::Bus>>,
        config: AgentConfig,
        system_prompt: String,
    ) -> Result<Self, BuildError> {
        let primary = buses[0].clone();
        let info = NodeInfo {
            node_id: NodeId::new(format!("engine/{}", config.agent_id)),
            node_type: "engine".into(),
            capabilities: serde_json::json!({
                "kind": "engine",
                "agent_id": config.agent_id,
            }),
            online_since: 0,
        };

        // Build Engine's filter — only response msg_types we care about.
        let types = engine_response_types(&config);
        let filter = arf_core::MessageFilter {
            types: if types.is_empty() { None } else { Some(types) },
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };

        let handle = primary
            .connect(info.clone(), filter)
            .await
            .map_err(|e| BuildError::PrimaryBusConnect(e.to_string()))?;

        Ok(Self {
            config,
            agent_id: info.node_id,
            handle,
            primary_bus: primary.clone(),
            system_prompt,
        })
    }

    /// Borrow the primary Bus Arc (used by Checkpoint evaluation to query
    /// `graph()` for Discovery-route resolution). Phase 6 task 6.5.
    pub fn primary_bus(&self) -> &Arc<arf_bus::Bus> {
        &self.primary_bus
    }

    pub fn config(&self) -> &AgentConfig { &self.config }
    pub fn system_prompt(&self) -> &str { &self.system_prompt }
    pub fn agent_id(&self) -> &NodeId { &self.agent_id }
    pub fn handle(&self) -> &NodeHandle { &self.handle }

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
        self.prepare_round(state, &user_input);

        loop {
            if cancel.is_cancelled() {
                return Err(RunError::Stopped);
            }

            // 终止：max_turns（每 turn 后判一次）
            if state.over_view.turn_count as u32 >= self.config.max_turns {
                return Err(RunError::MaxTurnsExceeded {
                    max_turns: self.config.max_turns,
                });
            }

            // ── Checkpoint::BeforeModelCall (6.5) ────────────────────────
            self.evaluate_and_dispatch(state, Checkpoint::BeforeModelCall, cancel.clone())
                .await?;
            if state.over_view.turn_count as u32 >= self.config.max_turns {
                return Err(RunError::MaxTurnsExceeded {
                    max_turns: self.config.max_turns,
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
            if state.over_view.turn_count as u32 >= self.config.max_turns {
                return Err(RunError::MaxTurnsExceeded {
                    max_turns: self.config.max_turns,
                });
            }

            // 终止：纯文本
            if tool_calls.is_empty() {
                // ── Checkpoint::RoundEnd (6.5) ───────────────────────────
                self.evaluate_and_dispatch(state, Checkpoint::RoundEnd, cancel.clone())
                    .await?;
                return Ok(content);
            }

            // tool_exec turns（sequential；6.6 加并发）
            for tc in tool_calls {
                if cancel.is_cancelled() {
                    return Err(RunError::Stopped);
                }

                // ── Checkpoint::BeforeToolExec (6.5) ───────────────────
                self.evaluate_and_dispatch(state, Checkpoint::BeforeToolExec, cancel.clone())
                    .await?;

                self.do_tool_turn(state, tc, cancel.clone()).await?;

                // ── Checkpoint::AfterToolExec (6.5) ────────────────────
                self.evaluate_and_dispatch(state, Checkpoint::AfterToolExec, cancel.clone())
                    .await?;

                if state.over_view.turn_count as u32 >= self.config.max_turns {
                    return Err(RunError::MaxTurnsExceeded {
                        max_turns: self.config.max_turns,
                    });
                }
            }
        }
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
        if self.config.checkpoint_rules.is_empty() {
            return Ok(());
        }
        // Build CheckpointMsg list without holding &mut self — keeps borrows disjoint.
        let graph_nodes = self.primary_bus.graph().nodes;
        let rules = &self.config.checkpoint_rules;
        let routes = &self.config.routes;
        let built = cp_eval::evaluate(state, trigger, rules, routes, &graph_nodes)?;

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
    async fn publish_only_command(
        &self,
        msg: &dyn ActionMessage,
        recipients: Vec<NodeId>,
    ) -> Result<(), RunError> {
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

    /// 推 system prompt + user message；inc_round
    fn prepare_round(&self, state: &mut State, user_input: &str) {
        if state.messages.is_empty() {
            state.push_message(ModelMessage::new("system", &self.system_prompt));
        } else if state.messages[0].role != "system" {
            state.messages
                .insert(0, ModelMessage::new("system", &self.system_prompt));
        }
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

        let model_call = ModelCall::new(state.messages.clone());
        let cid = model_call.correlation_id;
        let msg = Message::with_from_bus(
            model_call.msg_type(),
            self.agent_id.clone(),
            vec![],
            model_call.payload(),
            self.handle.primary_bus_id(),
        );

        let response = self.send_and_await(state, cid, msg, cancel).await?;

        // Parse
        let content = response
            .payload
            .get("content")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
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

        // Update context_tokens
        if let Some(usage) = response.payload.get("usage") {
            if let Some(tokens) = usage.get("prompt_tokens").and_then(|v| v.as_u64()) {
                state.set_context_tokens(tokens as usize);
            }
        }

        // Push assistant message（含 tool_calls）；不再 inc_turn（消息是响应非请求）
        let mut assistant_msg = ModelMessage::new("assistant", &content);
        assistant_msg.tool_calls = tool_calls.clone();
        state.push_message(assistant_msg);

        Ok((content, tool_calls))
    }

    /// Tool call turn: send + await tool_result + parse + append tool message.
    /// Each tool_exec is +1 to turn_count（已 send 即计）。
    async fn do_tool_turn(
        &mut self,
        state: &mut State,
        tc: ToolCall,
        cancel: CancellationToken,
    ) -> Result<serde_json::Value, RunError> {
        state.inc_turn();

        let target = tc.target.clone();
        let tool_exec = ToolExec {
            correlation_id: Uuid::new_v4(),
            tool_name: tc.name.clone(),
            arguments: tc.arguments.clone(),
            target: target.clone(),
        };
        let cid = tool_exec.correlation_id;
        let msg = Message::with_from_bus(
            tool_exec.msg_type(),
            self.agent_id.clone(),
            target.into_iter().collect(),
            tool_exec.payload(),
            self.handle.primary_bus_id(),
        );

        let response = self.send_and_await(state, cid, msg, cancel).await?;

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

        Ok(response.payload)
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
                continue;
            }
            let msg_cid = msg
                .payload
                .get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| Uuid::parse_str(s).ok());
            if msg_cid != Some(our_cid) {
                continue;
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
    for msg_type in config.routes.keys() {
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
        "memory_op" => Some("memory_op_result".into()),
        "human_handoff" => Some("human_handoff_reply".into()),
        _ => None,
    }
}
