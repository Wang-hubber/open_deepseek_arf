//! Application-level message protocol on top of wire `Message`.
//!
//! `ActionMessage` is the trait every payload-type must implement to ride
//! on the Bus. Built-in messages (`ModelCall`, `ToolExec`) implement it
//! directly; App code defines its own via `impl ActionMessage`.
//!
//! Phase 6 task 6.1: core type definitions.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{ModelMessage, ToolSpec, NodeId};

/// Core-side model inference parameters — kept in arf-core (not
/// arf-model-adapter) to avoid a cyclic crate dependency, while sharing the
/// exact JSON shape so `ModelCall` on the wire deserialises cleanly into
/// `arf_model_adapter::ModelCallPayload.model_params`.
///
/// Phase 9 F-005: this is how `ModelDecl.thinking_enabled` propagates from
/// the Engine config to the model adapter that actually invokes the LLM.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CoreModelParams {
    #[serde(default)]
    pub thinking_enabled: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_tokens: Option<u32>,
    // Round-trip safe: skip when null on serialize, default to Value::Null on
    // deserialize (matches the pre-F-005 wire format that omitted this field).
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub extra: serde_json::Value,
}

/// Trait every application-layer payload must implement.
///
/// Implementors define:
/// - `msg_type`: wire-level routing key (e.g., `"model_call"`, `"tool_exec"`)
/// - `correlation_id`: response matching key
/// - `payload`: serialized wire payload
/// - `intent`: whether Engine must wait for response (Query) or not (Command)
#[async_trait]
pub trait ActionMessage: Send + Sync {
    /// Wire-level msg_type for Bus routing.
    fn msg_type(&self) -> &'static str;

    /// Unique ID for response correlation.
    fn correlation_id(&self) -> Uuid;

    /// Serialize to wire payload (JSON).
    fn payload(&self) -> serde_json::Value;

    /// Query: Engine waits for the response (parks ReAct loop).
    /// Command: Engine doesn't wait; receiver processes asynchronously.
    fn intent(&self) -> MessageIntent;
}

/// Whether Engine waits for response (Query) or fires-and-forgets (Command).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum MessageIntent {
    /// Engine must wait for the response (e.g., `ModelCall`, `ToolExec`).
    Query,
    /// Engine doesn't wait; receiver processes asynchronously
    /// (e.g., `MemoryOp::extract`).
    Command,
}

// ── Built-in messages ────────────────────────────────────────────────

/// `Engine → ModelAdapter`: invoke an LLM with messages, expecting assistant reply.
///
/// Wire `msg_type`: `"model_call"`. Response wire `msg_type`: `"model_response"`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelCall {
    pub correlation_id: Uuid,
    /// Conversation history sent to the LLM.
    pub messages: Vec<ModelMessage>,
    /// Tool specs for LLM function-calling (Phase 6 §3.3).
    #[serde(default)]
    pub tools: Vec<ToolSpec>,
    /// Model inference parameters (Phase 9 F-005: propagation of
    /// `ModelDecl.thinking_enabled` etc.). Defaults to
    /// `CoreModelParams::default()` if missing — backward-compatible.
    #[serde(default)]
    pub model_params: CoreModelParams,
}

impl ModelCall {
    pub fn new(messages: Vec<ModelMessage>) -> Self {
        Self {
            correlation_id: Uuid::new_v4(),
            messages,
            tools: Vec::new(),
            model_params: CoreModelParams::default(),
        }
    }

    pub fn with_tools(mut self, tools: Vec<ToolSpec>) -> Self {
        self.tools = tools;
        self
    }

    /// Override model inference parameters (thinking, temperature, …).
    pub fn with_model_params(mut self, params: CoreModelParams) -> Self {
        self.model_params = params;
        self
    }
}

#[async_trait]
impl ActionMessage for ModelCall {
    fn msg_type(&self) -> &'static str {
        "model_call"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Query
    }
}

/// `Engine → ToolNode`: execute a tool call by `tool_name` with `arguments`.
///
/// Wire `msg_type`: `"tool_exec"`. Response wire `msg_type`: `"tool_result"`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolExec {
    pub correlation_id: Uuid,
    pub tool_name: String,
    pub arguments: serde_json::Value,
    /// Optional target NodeId (when route is Strict). None = use AgentConfig.routes.
    #[serde(default)]
    pub target: Option<NodeId>,
}

/// Tool call request from LLM (parsed from `model_response.tool_calls[i]`).
///
/// Phase 6 task 6.4: added so ReAct loop can deserialize model_response content.
/// `target` is the optional explicit NodeId (overrides AgentConfig.routes when set).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: serde_json::Value,
    #[serde(default)]
    pub target: Option<NodeId>,
}

impl ToolExec {
    pub fn new(tool_name: impl Into<String>, arguments: serde_json::Value) -> Self {
        Self {
            correlation_id: Uuid::new_v4(),
            tool_name: tool_name.into(),
            arguments,
            target: None,
        }
    }

    pub fn with_target(mut self, target: NodeId) -> Self {
        self.target = Some(target);
        self
    }
}

#[async_trait]
impl ActionMessage for ToolExec {
    fn msg_type(&self) -> &'static str {
        "tool_exec"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Query
    }
}

// ── Phase 8 ActionMessage set ─────────────────────────────────────────
//
// Five new protocol messages for the codecompass-fs example (Phase 8 task F1):
// - SubagentDelegate / SubagentResult  — parent Engine → child Engine
// - PeerMessage / PeerReply            — Engine ↔ Engine p2p coordination
// - MemoryOp / MemoryOpResult          — Engine → Memory Node CRUD
// - HumanHandoff / HumanHandoffReply   — Engine → UI/operator escalation
// - ModelResponseChunk                 — streaming LLM delta (Command intent)

/// `Engine → Subagent Engine`: delegate a sub-task to a nested Engine Node.
///
/// Wire `msg_type`: `"subagent_delegate"`. Response wire `msg_type`: `"subagent_result"`.
/// The child Engine runs to completion in its own session, then publishes
/// `SubagentResult` back to the parent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubagentDelegate {
    pub correlation_id: Uuid,
    pub parent_session_id: String,
    pub subagent_node_id: NodeId,
    pub task: String,
    #[serde(default)]
    pub context: serde_json::Value,
}

impl SubagentDelegate {
    pub fn new(
        parent_session_id: impl Into<String>,
        subagent_node_id: NodeId,
        task: impl Into<String>,
    ) -> Self {
        Self {
            correlation_id: Uuid::new_v4(),
            parent_session_id: parent_session_id.into(),
            subagent_node_id,
            task: task.into(),
            context: serde_json::Value::Null,
        }
    }

    pub fn with_context(mut self, context: serde_json::Value) -> Self {
        self.context = context;
        self
    }
}

#[async_trait]
impl ActionMessage for SubagentDelegate {
    fn msg_type(&self) -> &'static str {
        "subagent_delegate"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Query
    }
}

/// `Subagent → Parent Engine`: completion notice for a delegated sub-task.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubagentResult {
    pub correlation_id: Uuid,
    pub status: SubagentStatus,
    pub output: String,
    #[serde(default)]
    pub trajectory: Vec<ModelMessage>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum SubagentStatus {
    Success,
    Failed,
    Cancelled,
}

impl SubagentResult {
    pub fn success(correlation_id: Uuid, output: impl Into<String>) -> Self {
        Self {
            correlation_id,
            status: SubagentStatus::Success,
            output: output.into(),
            trajectory: Vec::new(),
        }
    }

    pub fn failed(correlation_id: Uuid, error: impl Into<String>) -> Self {
        Self {
            correlation_id,
            status: SubagentStatus::Failed,
            output: error.into(),
            trajectory: Vec::new(),
        }
    }
}

#[async_trait]
impl ActionMessage for SubagentResult {
    fn msg_type(&self) -> &'static str {
        "subagent_result"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Command
    }
}

/// `Engine ↔ Engine`: 1:1 directed message between peer sessions.
///
/// Wire `msg_type`: `"peer_message"`. Response wire `msg_type`: `"peer_reply"`.
/// Routes by session_id (parent owns PeerCoordinator; receivers handle via Engine's
/// pre-built message filter that listens for `peer_message`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerMessage {
    pub correlation_id: Uuid,
    pub from_session: String,
    pub to_session: String,
    pub content: String,
    #[serde(default)]
    pub attachments: Vec<serde_json::Value>,
}

impl PeerMessage {
    pub fn new(
        from_session: impl Into<String>,
        to_session: impl Into<String>,
        content: impl Into<String>,
    ) -> Self {
        Self {
            correlation_id: Uuid::new_v4(),
            from_session: from_session.into(),
            to_session: to_session.into(),
            content: content.into(),
            attachments: Vec::new(),
        }
    }
}

#[async_trait]
impl ActionMessage for PeerMessage {
    fn msg_type(&self) -> &'static str {
        "peer_message"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Query
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerReply {
    pub correlation_id: Uuid,
    pub status: PeerStatus,
    pub content: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum PeerStatus {
    Ok,
    Failed,
    Refused,
}

impl PeerReply {
    pub fn ok(correlation_id: Uuid, content: impl Into<String>) -> Self {
        Self {
            correlation_id,
            status: PeerStatus::Ok,
            content: content.into(),
        }
    }
}

#[async_trait]
impl ActionMessage for PeerReply {
    fn msg_type(&self) -> &'static str {
        "peer_reply"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Command
    }
}

/// `Engine → Memory Node`: key-value CRUD on persistent memory.
///
/// Wire `msg_type`: `"memory_op"`. Response wire `msg_type`: `"memory_op_result"`.
/// Memory Node is a standard ARF Node that implements ActionMessage handler for
/// `memory_op` and emits `memory_op_result`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryOp {
    pub correlation_id: Uuid,
    pub op: MemoryOpKind,
    pub key: String,
    #[serde(default)]
    pub value: serde_json::Value,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum MemoryOpKind {
    Read,
    Write,
    Delete,
    List,
}

impl MemoryOp {
    pub fn read(key: impl Into<String>) -> Self {
        Self {
            correlation_id: Uuid::new_v4(),
            op: MemoryOpKind::Read,
            key: key.into(),
            value: serde_json::Value::Null,
        }
    }

    pub fn write(key: impl Into<String>, value: serde_json::Value) -> Self {
        Self {
            correlation_id: Uuid::new_v4(),
            op: MemoryOpKind::Write,
            key: key.into(),
            value,
        }
    }

    pub fn delete(key: impl Into<String>) -> Self {
        Self {
            correlation_id: Uuid::new_v4(),
            op: MemoryOpKind::Delete,
            key: key.into(),
            value: serde_json::Value::Null,
        }
    }
}

#[async_trait]
impl ActionMessage for MemoryOp {
    fn msg_type(&self) -> &'static str {
        "memory_op"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Query
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryOpResult {
    pub correlation_id: Uuid,
    pub ok: bool,
    #[serde(default)]
    pub value: serde_json::Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl MemoryOpResult {
    pub fn found(correlation_id: Uuid, value: serde_json::Value) -> Self {
        Self {
            correlation_id,
            ok: true,
            value,
            error: None,
        }
    }

    pub fn missing(correlation_id: Uuid) -> Self {
        Self {
            correlation_id,
            ok: false,
            value: serde_json::Value::Null,
            error: None,
        }
    }
}

#[async_trait]
impl ActionMessage for MemoryOpResult {
    fn msg_type(&self) -> &'static str {
        "memory_op_result"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Command
    }
}

/// `Engine → Human/UI`: ask for human input on a decision the model can't make.
///
/// Wire `msg_type`: `"human_handoff"`. Response wire `msg_type`: `"human_handoff_reply"`.
/// In the codecompass-fs example the reply is synthetically generated by the CLI
/// (CLI prompts the user); in production this would route to a UI/web channel.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HumanHandoff {
    pub correlation_id: Uuid,
    pub question: String,
    #[serde(default)]
    pub context: serde_json::Value,
    #[serde(default)]
    pub options: Vec<String>,
}

impl HumanHandoff {
    pub fn new(question: impl Into<String>) -> Self {
        Self {
            correlation_id: Uuid::new_v4(),
            question: question.into(),
            context: serde_json::Value::Null,
            options: Vec::new(),
        }
    }
}

#[async_trait]
impl ActionMessage for HumanHandoff {
    fn msg_type(&self) -> &'static str {
        "human_handoff"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Query
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HumanHandoffReply {
    pub correlation_id: Uuid,
    pub answer: String,
    #[serde(default)]
    pub selected_option: Option<usize>,
}

impl HumanHandoffReply {
    pub fn new(correlation_id: Uuid, answer: impl Into<String>) -> Self {
        Self {
            correlation_id,
            answer: answer.into(),
            selected_option: None,
        }
    }
}

#[async_trait]
impl ActionMessage for HumanHandoffReply {
    fn msg_type(&self) -> &'static str {
        "human_handoff_reply"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Command
    }
}

/// `ModelAdapter → Engine`: incremental LLM output chunk (streaming).
///
/// Wire `msg_type`: `"model_response_chunk"`. Engine aggregates chunks into a
/// single `model_response` before resuming the ReAct loop. No response expected.
///
/// # `thinking_enabled` and `reasoning_delta`
///
/// The chunk itself does **not** carry `thinking_enabled` (it is a per-call
/// config flag set on the originating `model_call`). Whether a chunk's
/// `reasoning_delta` is populated depends entirely on the upstream provider:
/// - When the model was called with `ModelCall::with_model_params(
///   CoreModelParams { thinking_enabled: true, .. })`, providers that
///   support reasoning (qwen thinking mode, DeepSeek reasoning, etc.)
///   emit non-empty `reasoning_delta` chunks.
/// - When `thinking_enabled = false`, the provider emits only
///   `content_delta`; `reasoning_delta` is empty.
///
/// Audit note (Phase 9 F-005 YELLOW): the chunk **return** path does not
/// need to re-declare `thinking_enabled` because the decision is upstream
/// of the chunk stream — providers control their own reasoning emission
/// behavior. The outbound `model_call` already carries `model_params`
/// (F-005 fix, 7074249), so the call↔chunk semantic link is preserved
/// via the `correlation_id` alone.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelResponseChunk {
    pub correlation_id: Uuid,
    pub content_delta: String,
    #[serde(default)]
    pub reasoning_delta: String,
    #[serde(default)]
    pub tool_call_delta: Option<ModelToolCallDelta>,
    pub finished: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelToolCallDelta {
    pub id: String,
    pub name_delta: String,
    pub arguments_delta: String,
}

impl ModelResponseChunk {
    pub fn text(correlation_id: Uuid, delta: impl Into<String>) -> Self {
        Self {
            correlation_id,
            content_delta: delta.into(),
            reasoning_delta: String::new(),
            tool_call_delta: None,
            finished: false,
        }
    }

    pub fn finish(correlation_id: Uuid) -> Self {
        Self {
            correlation_id,
            content_delta: String::new(),
            reasoning_delta: String::new(),
            tool_call_delta: None,
            finished: true,
        }
    }
}

#[async_trait]
impl ActionMessage for ModelResponseChunk {
    fn msg_type(&self) -> &'static str {
        "model_response_chunk"
    }
    fn correlation_id(&self) -> Uuid {
        self.correlation_id
    }
    fn payload(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
    fn intent(&self) -> MessageIntent {
        MessageIntent::Command
    }
}
