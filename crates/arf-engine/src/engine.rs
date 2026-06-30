//! Engine — ReAct 循环 actor（Phase 6 §3 / §6.4）。

use std::collections::HashMap;
use std::sync::Arc;

use arf_bus::NodeHandle;
use arf_core::{
    ActionMessage, Message, ModelCall, ModelMessage, NodeId, NodeInfo, State,
    ToMatch,
};
use tokio::sync::{oneshot, Mutex};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use crate::config::AgentConfig;
use crate::error::{BuildError, RunError};

const MODEL_RESPONSE: &str = "model_response";
const MAX_RECURSION: usize = 1; // 6.3 仅 1 round；6.4 多 turn

/// ReAct loop actor. Phase 6 §0.1 — 设计文档明确：
/// "Engine 是 Bus 上的一个 Actor: 维护 AgentConfig + State, 在 ReAct 循环中按订阅式触发器收发消息。"
/// 6.3 实现最小骨架；6.4 实现完整 ReAct 循环。
pub struct Engine {
    config: AgentConfig,
    agent_id: NodeId,
    /// Connection to primary Bus (other Buses via attach — 6.3 暂未 attach)
    handle: NodeHandle,
    /// correlation_id → oneshot::Sender for in-flight model_call waits
    response_waits: Arc<Mutex<HashMap<Uuid, oneshot::Sender<serde_json::Value>>>>,
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
        // Empty list → `types: None` means "all messages" (otherwise Some(vec![])
        // would reject everything).
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
            response_waits: Arc::new(Mutex::new(HashMap::new())),
            system_prompt,
        })
    }

    pub fn config(&self) -> &AgentConfig {
        &self.config
    }
    pub fn system_prompt(&self) -> &str {
        &self.system_prompt
    }
    pub fn agent_id(&self) -> &NodeId {
        &self.agent_id
    }
    pub fn handle(&self) -> &NodeHandle {
        &self.handle
    }

    /// 1 round ReAct: user → model_call → response → append assistant → return content.
    /// Phase 6 task 6.3 minimal scope (not the full multi-turn loop).
    pub async fn run(
        &mut self,
        state: &mut State,
        user_input: String,
        cancel: CancellationToken,
    ) -> Result<String, RunError> {
        // 1. push user message; prefill system prompt if first call
        if state.messages.is_empty() {
            state.push_message(ModelMessage::new("system", &self.system_prompt));
        } else if state.messages[0].role != "system" {
            state.messages
                .insert(0, ModelMessage::new("system", &self.system_prompt));
        }
        state.push_message(ModelMessage::new("user", &user_input));
        state.over_view.last_user_message = user_input.clone();
        state.inc_round();
        state.inc_turn();

        // 2. turn_count cap (6.3 minimal — 1 round only)
        let max_one_round: usize = state.over_view.round_count + MAX_RECURSION;
        if state.over_view.turn_count >= max_one_round {
            return Err(RunError::MaxTurnsExceeded {
                max_turns: self.config.max_turns,
            });
        }

        // 3. construct ModelCall
        let model_call = ModelCall::new(state.messages.clone());
        let cid = model_call.correlation_id;

        // 4. wire msg: 从 Engine 发出 → primary_bus_id stamp
        let msg = Message::with_from_bus(
            model_call.msg_type(),
            self.agent_id.clone(),
            vec![], // empty to=[] = broadcast; route resolves via AgentConfig.routes
            model_call.payload(),
            self.handle.primary_bus_id(),
        );

        // 5. register oneshot for this correlation_id
        let (tx, _rx) = oneshot::channel();
        self.response_waits.lock().await.insert(cid, tx);

        // 6. send
        if let Err(e) = self.handle.send_message(msg).await {
            self.response_waits.lock().await.remove(&cid);
            return Err(RunError::Bus(e));
        }

        // 7. await response (with cancellation)
        let response_msg = tokio::select! {
            r = self.wait_for_response(cid) => r?,
            _ = cancel.cancelled() => {
                self.response_waits.lock().await.remove(&cid);
                return Err(RunError::Stopped);
            }
        };

        // 8. parse content + usage
        let content = response_msg
            .payload
            .get("content")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if let Some(usage) = response_msg.payload.get("usage") {
            if let Some(tokens) = usage.get("prompt_tokens").and_then(|v| v.as_u64()) {
                state.set_context_tokens(tokens as usize);
            }
        }

        // 9. append assistant message
        state.push_message(ModelMessage::new("assistant", content.clone()));
        state.inc_turn();

        Ok(content)
    }

    /// Loop on handle.recv; forward matching model_response (by correlation_id)
    /// into our oneshot. Other msg_types are skipped.
    async fn wait_for_response(
        &mut self,
        cid: Uuid,
    ) -> Result<Message, RunError> {
        loop {
            let msg = self.handle.recv().await.map_err(|_| {
                RunError::Internal("handle closed".into())
            })?;
            if msg.msg_type == MODEL_RESPONSE {
                if let Some(payload_cid) = msg
                    .payload
                    .get("correlation_id")
                    .and_then(|v| v.as_str())
                    .and_then(|s| Uuid::parse_str(s).ok())
                {
                    if payload_cid == cid {
                        return Ok(msg);
                    }
                }
            }
            // else: skip non-matching or non-response messages
        }
    }

    /// Test hook: directly inject a response payload (bypassing Bus).
    pub async fn inject_response(&self, cid: Uuid, payload: serde_json::Value) {
        if let Some(tx) = self.response_waits.lock().await.remove(&cid) {
            let _ = tx.send(payload);
        }
    }
}

/// Engine wants the response msg_types (反向映射 request → response）。
/// Phase 6 §1.2 白名单：model_response / tool_result / memory_op_result / human_handoff_reply。
fn engine_response_types(config: &AgentConfig) -> Vec<String> {
    let mut types: Vec<String> = config
        .routes
        .keys()
        .filter_map(|t| response_msg_type_for(t))
        .collect();
    if config.routes.contains_key("model_call") {
        types.push("model_response".into());
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

// Unused for now but referenced from `engine_response_types` indirectly.
fn _bus_id_marker() {}
