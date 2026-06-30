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
}

impl ModelCall {
    pub fn new(messages: Vec<ModelMessage>) -> Self {
        Self {
            correlation_id: Uuid::new_v4(),
            messages,
            tools: Vec::new(),
        }
    }

    pub fn with_tools(mut self, tools: Vec<ToolSpec>) -> Self {
        self.tools = tools;
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
