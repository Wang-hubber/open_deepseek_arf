//! ARF shared types — Message format, identifiers, error types, Node trait.
//!
//! This crate defines the common vocabulary that all other ARF crates share.
//! It has zero dependencies on other ARF crates.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub mod node;
// Re-export BusId so downstream crates don't need to know about the `node` module layout.
pub use node::BusId;

// ── NodeId ───────────────────────────────────────────────────────────

/// Unique identifier for a node on the Bus.
///
/// Convention: `"{node_type}/{name}"`, e.g. `"engine/main"`, `"mcp/filesystem"`.
#[derive(Debug, Clone, Hash, Eq, PartialEq, Ord, PartialOrd, Serialize, Deserialize)]
pub struct NodeId(pub String);

impl NodeId {
    /// Create a new NodeId from anything that can become a String.
    pub fn new(id: impl Into<String>) -> Self {
        Self(id.into())
    }

    /// Borrow as `&str`.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for NodeId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

// ── Message ───────────────────────────────────────────────────────────

/// The universal message format on the Bus.
///
/// Every event — node lifecycle, model call, tool execution, action —
/// flows through the Bus as a `Message`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    /// Unique message ID.
    pub id: Uuid,
    /// Message type: `"node_online"`, `"model_call"`, `"action"`, etc.
    pub msg_type: String,
    /// Sender node ID.
    pub from: NodeId,
    /// Receiver targets. Empty = broadcast to all.
    pub to: Vec<NodeId>,
    /// Arbitrary JSON payload.
    pub payload: serde_json::Value,
    /// Source Bus identifier (Phase 6 multi-Bus). `Some` for messages that
    /// passed through a Bus (lifecyle, send, broadcast). `None` only for
    /// messages constructed directly (tests, internal scaffolding).
    ///
    /// `#[serde(default)]` allows backward-compatible deserialization of
    /// Phase 1 historical payloads that lack this field. `skip_serializing_if`
    /// keeps the wire format clean when `None`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub from_bus: Option<crate::node::BusId>,
    /// Unix timestamp in milliseconds.
    pub timestamp: u64,
}

impl Message {
    /// Create a new message with the current timestamp.
    /// `from_bus` defaults to `None`; use `with_from_bus` for Bus-stamped messages.
    pub fn new(
        msg_type: impl Into<String>,
        from: NodeId,
        to: Vec<NodeId>,
        payload: serde_json::Value,
    ) -> Self {
        Self {
            id: Uuid::new_v4(),
            msg_type: msg_type.into(),
            from,
            to,
            payload,
            from_bus: None,
            timestamp: now_ms(),
        }
    }

    /// Construct a Message with an explicit `from_bus` stamp.
    /// Used by Bus-internal broadcast sites (handle_connect, send_via,
    /// heartbeat tick).
    pub fn with_from_bus(
        msg_type: impl Into<String>,
        from: NodeId,
        to: Vec<NodeId>,
        payload: serde_json::Value,
        from_bus: crate::node::BusId,
    ) -> Self {
        let mut m = Self::new(msg_type, from, to, payload);
        m.from_bus = Some(from_bus);
        m
    }

    /// Returns true if this is a broadcast message (no specific targets).
    pub fn is_broadcast(&self) -> bool {
        self.to.is_empty()
    }

    /// Returns true if this message is directed at the given node.
    pub fn is_for(&self, node_id: &NodeId) -> bool {
        self.to.contains(node_id)
    }
}

/// Get current Unix timestamp in milliseconds.
fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

// ── NodeInfo ──────────────────────────────────────────────────────────

/// Information broadcast when a node comes online.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeInfo {
    pub node_id: NodeId,
    /// Node type: `"engine"`, `"mcp"`, `"model"`, `"trace"`.
    /// Engine = Agent + State = one active session.
    pub node_type: String,
    /// Capabilities: MCP declares `{"resources": ["prompt/...", "tool/...", "skill/..."]}`;
    /// Engine declares `{"sessions": ["sid1", "sid2"]}`.
    pub capabilities: serde_json::Value,
    /// Milliseconds since Unix epoch when this node connected.
    pub online_since: u64,
}

// ── BusGraph ──────────────────────────────────────────────────────────

/// Snapshot of the Bus health at query time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BusGraph {
    pub nodes: Vec<NodeInfo>,
    pub message_count: u64,
    pub uptime_ms: u64,
}

// ── SendReceipt ───────────────────────────────────────────────────────

/// Delivery receipt returned by `NodeHandle::send()`.
#[derive(Debug, Clone, PartialEq)]
pub struct SendReceipt {
    /// The ID of the message that was sent.
    pub message_id: Uuid,
    /// Number of nodes online at the moment of send.
    pub online_nodes: usize,
    /// Number of online nodes whose filter potentially matches this message.
    pub matching_nodes: usize,
}

// ── MessageFilter ─────────────────────────────────────────────────────

/// Controls which messages a node receives from the Bus.
#[derive(Debug, Clone, PartialEq)]
pub struct MessageFilter {
    /// If set, only messages of these types pass through.
    /// `None` means accept all types.
    pub types: Option<Vec<String>>,
    /// How to handle `to`-based filtering.
    pub to_match: ToMatch,
}

/// How a node's filter matches the `to` field on messages.
#[derive(Debug, Clone, PartialEq)]
pub enum ToMatch {
    /// Receive all messages regardless of `to`.
    All,
    /// Only receive messages with `to` empty (broadcast).
    BroadcastOnly,
    /// Only receive messages directed specifically to this node.
    DirectedToMe,
    /// Receive broadcasts AND messages directed to this node. (default)
    BroadcastAndDirectedToMe,
}

impl MessageFilter {
    /// Returns true if the message passes this filter.
    ///
    /// - `types`: if `Some`, the message's `msg_type` must be in the list.
    /// - `to_match`: controls how `msg.to` is matched against `node_id`.
    pub fn matches(&self, msg: &Message, node_id: &NodeId) -> bool {
        // 1. Type filter
        if let Some(ref types) = self.types
            && !types.contains(&msg.msg_type)
        {
            return false;
        }

        // 2. Target filter
        match self.to_match {
            ToMatch::All => true,
            ToMatch::BroadcastOnly => msg.to.is_empty(),
            ToMatch::DirectedToMe => msg.to.contains(node_id),
            ToMatch::BroadcastAndDirectedToMe => msg.to.is_empty() || msg.to.contains(node_id),
        }
    }
}

// ── SendError ─────────────────────────────────────────────────────────

/// Errors that can occur when sending a message.
///
/// Note: there is no "buffer full" variant. The bus uses a `tokio::sync::broadcast`
/// channel with CAN-bus semantics — a slow consumer receives `Lagged(n)` rather than
/// blocking the sender. The sender never waits and never sees backpressure.
#[derive(Debug)]
pub enum SendError {
    /// All directed message targets are offline.
    NodeOffline(Vec<NodeId>),
    /// Bus has been shut down.
    BusClosed,
    /// `send_via` was called with a `BusId` that this handle is not attached to.
    /// (Phase 6 multi-Bus — added in task 6.0.2.)
    NoSuchBus(crate::node::BusId),
}

impl std::fmt::Display for SendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NodeOffline(ids) => {
                let names: Vec<_> = ids.iter().map(|id| id.as_str()).collect();
                write!(f, "target nodes offline: {}", names.join(", "))
            }
            Self::BusClosed => write!(f, "bus closed"),
            Self::NoSuchBus(bid) => write!(f, "no such bus: {bid}"),
        }
    }
}

impl std::error::Error for SendError {}

// ── TaskId ──────────────────────────────────────────────────────────

/// A2A task identifier.
///
/// UUID ensures global uniqueness across sessions.
/// `owner` records which node created the task.
#[derive(Debug, Clone, Hash, Eq, PartialEq, Serialize, Deserialize)]
pub struct TaskId {
    pub id: Uuid,
    pub owner: NodeId,
}

impl TaskId {
    /// Create a new TaskId with a random UUID and the given owner.
    pub fn new(owner: NodeId) -> Self {
        Self {
            id: Uuid::new_v4(),
            owner,
        }
    }
}

impl std::fmt::Display for TaskId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}@{}", self.id, self.owner)
    }
}

// ── TaskStatus ───────────────────────────────────────────────────────

/// Task lifecycle status.
///
/// ```text
/// created ──→ in_progress ──→ resolved
///     │            │  ↑          failed
///     │            │  │ 唤醒     cancelled
///     │            │  │
///     └──→ blocked ──┘
///               │
///               ├──→ cancelled (级联取消)
///               └──→ failed    (节点离线)
/// ```
///
/// Resolved / Failed / Cancelled are terminal states — irreversible.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum TaskStatus {
    Created,
    InProgress,
    Blocked,
    Resolved,
    Failed,
    Cancelled,
}

impl TaskStatus {
    /// Returns true if this is a terminal state.
    ///
    /// Terminal states cannot transition further.
    pub fn is_terminal(&self) -> bool {
        matches!(self, Self::Resolved | Self::Failed | Self::Cancelled)
    }
}

impl std::fmt::Display for TaskStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Self::Created => "created",
            Self::InProgress => "in_progress",
            Self::Blocked => "blocked",
            Self::Resolved => "resolved",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        };
        write!(f, "{s}")
    }
}

// ── ModelMessage ─────────────────────────────────────────────────────

/// A single message in the model conversation history.
///
/// `extra` carries provider-specific opaque data (e.g., DeepSeek
/// `reasoning_content`). State stores it as-is; ModelAdapter reads/writes
/// it — no other component interprets it. Phase 5 (ModelAdapter) will add
/// `tool_calls: Vec<ToolCall>` for assistant parallel tool invocations.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelMessage {
    /// Role: `"user"`, `"assistant"`, `"system"`, or `"tool"`.
    pub role: String,
    /// Message content.
    pub content: String,
    /// Required when role is "tool" — the ID of the tool call this result belongs to.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    /// Optional display name (e.g., function name for tool role, author for user).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// Provider-specific opaque data. Managed entirely by ModelAdapter.
    /// State stores it without interpretation. Default to `Value::Null`.
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub extra: serde_json::Value,
}

impl ModelMessage {
    /// Create a ModelMessage with role and content.
    ///
    /// `tool_call_id`, `name`, and `extra` default to None/Null;
    /// set them via the builder-style methods below.
    pub fn new(role: impl Into<String>, content: impl Into<String>) -> Self {
        Self {
            role: role.into(),
            content: content.into(),
            tool_call_id: None,
            name: None,
            extra: serde_json::Value::Null,
        }
    }

    /// Set `tool_call_id` (builder style).
    pub fn with_tool_call_id(mut self, id: impl Into<String>) -> Self {
        self.tool_call_id = Some(id.into());
        self
    }

    /// Set `name` (builder style).
    pub fn with_name(mut self, name: impl Into<String>) -> Self {
        self.name = Some(name.into());
        self
    }

    /// Set `extra` (builder style). Managed by ModelAdapter.
    pub fn with_extra(mut self, extra: serde_json::Value) -> Self {
        self.extra = extra;
        self
    }
}

// ── Tests ─────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    // ═══════════════════════════════════════════════════════════════
    // NodeId — 10 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] 正常输入：new() 创建后 as_str() 返回相同值
    #[test]
    fn node_id_new_and_as_str() {
        let id = NodeId::new("engine/main");
        assert_eq!(id.as_str(), "engine/main");
    }

    // [trait] Display：format! 宏对 NodeId 的输出等于内部字符串
    #[test]
    fn node_id_display() {
        let id = NodeId::new("trace/observer");
        assert_eq!(format!("{id}"), "trace/observer");
    }

    // [边界] 空字符串：new("") 不 panic，as_str() 返回空串，Display 也空
    #[test]
    fn node_id_empty_string() {
        let id = NodeId::new("");
        assert_eq!(id.as_str(), "");
        assert_eq!(format!("{id}"), "");
    }

    // [边界] 超长字符串：1024 字符的 id 不截断、不 panic
    #[test]
    fn node_id_very_long() {
        let long = "a".repeat(1024);
        let id = NodeId::new(long.clone());
        assert_eq!(id.as_str(), &long);
    }

    // [边界] Unicode：中文、分隔符等非 ASCII 字符正常存取
    #[test]
    fn node_id_unicode() {
        let id = NodeId::new("引擎/主节点");
        assert_eq!(id.as_str(), "引擎/主节点");
    }

    // [trait] Eq：内容相同的两个 NodeId 相等，不同的不等
    #[test]
    fn node_id_equality() {
        let a = NodeId::new("a");
        let b = NodeId::new("a");
        let c = NodeId::new("c");
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    // [trait] Clone：clone 后的值与原始值相等
    #[test]
    fn node_id_clone_is_equal() {
        let a = NodeId::new("x");
        let b = a.clone();
        assert_eq!(a, b);
    }

    // [trait] Hash：NodeId 可作为 HashMap 的 key，相同内容命中，不存在返回 None
    #[test]
    fn node_id_hashable() {
        let mut map = HashMap::new();
        map.insert(NodeId::new("k1"), 1);
        map.insert(NodeId::new("k2"), 2);
        assert_eq!(map.get(&NodeId::new("k1")), Some(&1));
        assert_eq!(map.get(&NodeId::new("k3")), None);
    }

    // [trait] Ord：字典序排序，"a" < "aa" < "b"
    #[test]
    fn node_id_ordering() {
        let a = NodeId::new("a");
        let b = NodeId::new("b");
        let aa = NodeId::new("aa");
        assert!(a < aa);
        assert!(aa < b);
    }

    // [序列化] serde 往返：NodeId → JSON 字符串 → NodeId，值不变
    #[test]
    fn node_id_serialization_roundtrip() {
        let id = NodeId::new("mcp/filesystem");
        let json = serde_json::to_string(&id).unwrap();
        assert_eq!(json, r#""mcp/filesystem""#);
        let back: NodeId = serde_json::from_str(&json).unwrap();
        assert_eq!(id, back);
    }

    // ═══════════════════════════════════════════════════════════════
    // Message — construction — 6 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] new() 自动填充 id（非 nil）和 timestamp（> 0）
    #[test]
    fn message_new_fills_id_and_timestamp() {
        let from = NodeId::new("test");
        let msg = Message::new(
            "test_type",
            from.clone(),
            vec![],
            serde_json::json!({"k": "v"}),
        );
        assert!(!msg.id.is_nil());
        assert!(msg.timestamp > 0);
    }

    // [唯一性] 连续两次 new() 生成的 id 不同（UUID v4 的随机性保证）
    #[test]
    fn message_id_is_unique_each_call() {
        let from = NodeId::new("test");
        let m1 = Message::new("t", from.clone(), vec![], serde_json::json!(null));
        let m2 = Message::new("t", from.clone(), vec![], serde_json::json!(null));
        assert_ne!(m1.id, m2.id);
    }

    // [时间] 间隔 1ms 创建的两条消息，后者的 timestamp ≥ 前者
    #[test]
    fn message_timestamp_is_monotonic() {
        let from = NodeId::new("test");
        let m1 = Message::new("t", from.clone(), vec![], serde_json::json!(null));
        std::thread::sleep(std::time::Duration::from_millis(1));
        let m2 = Message::new("t", from.clone(), vec![], serde_json::json!(null));
        assert!(m2.timestamp >= m1.timestamp);
    }

    // [边界] msg_type 为空字符串：不 panic，行为与正常字符串一致
    #[test]
    fn message_empty_msg_type() {
        let from = NodeId::new("test");
        let msg = Message::new("", from.clone(), vec![], serde_json::json!(null));
        assert_eq!(msg.msg_type, "");
    }

    // [trait] Into<String>：传 owned String 而非 &str，msg_type 正确传递
    #[test]
    fn message_msg_type_from_string() {
        let from = NodeId::new("test");
        let s = String::from("model_call");
        let msg = Message::new(s, from.clone(), vec![], serde_json::json!(null));
        assert_eq!(msg.msg_type, "model_call");
    }

    // [字段] from 字段正确赋值为传入的 NodeId
    #[test]
    fn message_from_field_set_correctly() {
        let from = NodeId::new("sender");
        let msg = Message::new("t", from.clone(), vec![], serde_json::json!(null));
        assert_eq!(msg.from, from);
    }

    // ═══════════════════════════════════════════════════════════════
    // Message — is_broadcast / is_for — 6 tests
    // ═══════════════════════════════════════════════════════════════

    // [方法] to=[] 时 is_broadcast() 返回 true
    #[test]
    fn message_is_broadcast_when_to_is_empty() {
        let msg = Message::new("t", NodeId::new("a"), vec![], serde_json::json!(null));
        assert!(msg.is_broadcast());
    }

    // [方法] to=Some 时 is_broadcast() 返回 false
    #[test]
    fn message_is_not_broadcast_when_to_is_nonempty() {
        let msg = Message::new(
            "t",
            NodeId::new("a"),
            vec![NodeId::new("b")],
            serde_json::json!(null),
        );
        assert!(!msg.is_broadcast());
    }

    // [方法] 定向消息的目标节点调用 is_for() 返回 true
    #[test]
    fn message_is_for_target() {
        let target = NodeId::new("target");
        let msg = Message::new(
            "t",
            NodeId::new("sender"),
            vec![target.clone()],
            serde_json::json!(null),
        );
        assert!(msg.is_for(&target));
    }

    // [方法] 定向消息的非目标节点调用 is_for() 返回 false
    #[test]
    fn message_is_for_wrong_target() {
        let msg = Message::new(
            "t",
            NodeId::new("sender"),
            vec![NodeId::new("target")],
            serde_json::json!(null),
        );
        assert!(!msg.is_for(&NodeId::new("other")));
    }

    // [边界] 广播消息对任何节点（包括发送者自己）is_for() 都返回 false
    #[test]
    fn message_broadcast_is_not_for_anyone() {
        let msg = Message::new("t", NodeId::new("sender"), vec![], serde_json::json!(null));
        assert!(!msg.is_for(&NodeId::new("anyone")));
        assert!(!msg.is_for(&NodeId::new("sender")));
    }

    // [边界] 定向给自己：发送者也在 to 列表中，is_for 返回 true
    #[test]
    fn message_directed_to_self() {
        let self_id = NodeId::new("self");
        let msg = Message::new(
            "t",
            self_id.clone(),
            vec![self_id.clone()],
            serde_json::json!(null),
        );
        assert!(msg.is_for(&self_id));
        assert!(!msg.is_broadcast());
    }

    // ═══════════════════════════════════════════════════════════════
    // Message — serialization — 6 tests
    // ═══════════════════════════════════════════════════════════════

    // [序列化] 定向消息 serde 往返：所有字段（含 payload）逐项相等
    #[test]
    fn message_serialization_roundtrip_directed() {
        let msg = Message::new(
            "test",
            NodeId::new("sender"),
            vec![NodeId::new("receiver")],
            serde_json::json!({"key": [1, 2, 3]}),
        );
        let json = serde_json::to_string(&msg).unwrap();
        let back: Message = serde_json::from_str(&json).unwrap();
        assert_eq!(msg.id, back.id);
        assert_eq!(msg.msg_type, back.msg_type);
        assert_eq!(msg.from, back.from);
        assert_eq!(msg.to, back.to);
        assert_eq!(msg.timestamp, back.timestamp);
        assert_eq!(msg.payload, back.payload);
    }

    // [序列化] 广播消息（to=[]）serde 往返后 to 仍为空
    #[test]
    fn message_serialization_roundtrip_broadcast() {
        let msg = Message::new(
            "node_online",
            NodeId::new("engine/main"),
            vec![],
            serde_json::json!({"node": "info"}),
        );
        let json = serde_json::to_string(&msg).unwrap();
        let back: Message = serde_json::from_str(&json).unwrap();
        assert_eq!(msg.to, vec![]);
        assert_eq!(back.to, vec![]);
    }

    // [边界] payload 为 JSON null：序列化/反序列化后保持为 null
    #[test]
    fn message_serialization_null_payload() {
        let msg = Message::new("t", NodeId::new("a"), vec![], serde_json::Value::Null);
        let json = serde_json::to_string(&msg).unwrap();
        let back: Message = serde_json::from_str(&json).unwrap();
        assert_eq!(back.payload, serde_json::Value::Null);
    }

    // [边界] payload 深度嵌套（3 层 + 数组 + null + bool）：结构不丢失
    #[test]
    fn message_serialization_deeply_nested_payload() {
        let payload = serde_json::json!({
            "level1": {
                "level2": {
                    "level3": [1, null, "str", true, {"deep": []}]
                }
            }
        });
        let msg = Message::new("t", NodeId::new("a"), vec![], payload);
        let json = serde_json::to_string(&msg).unwrap();
        let back: Message = serde_json::from_str(&json).unwrap();
        assert_eq!(msg.payload, back.payload);
    }

    // [兼容] 时间戳为 0 的 JSON（旧版本/外部来源）反序列化不报错
    #[test]
    fn message_deserialize_missing_timestamp_still_works() {
        let json = r#"{"id":"00000000-0000-0000-0000-000000000000","msg_type":"t","from":"a","to":[],"payload":null,"timestamp":0}"#;
        let msg: Message = serde_json::from_str(json).unwrap();
        assert_eq!(msg.timestamp, 0);
    }

    // [trait] Clone：Message 的所有字段（含 payload）克隆后一致
    #[test]
    fn message_clone_produces_equal() {
        let msg = Message::new(
            "t",
            NodeId::new("a"),
            vec![NodeId::new("b")],
            serde_json::json!({"x": 1}),
        );
        let cloned = msg.clone();
        assert_eq!(msg.id, cloned.id);
        assert_eq!(msg.msg_type, cloned.msg_type);
        assert_eq!(msg.from, cloned.from);
        assert_eq!(msg.to, cloned.to);
        assert_eq!(msg.payload, cloned.payload);
        assert_eq!(msg.timestamp, cloned.timestamp);
    }

    // ═══════════════════════════════════════════════════════════════
    // Message — from_bus (Phase 6 task 6.0.3) — 5 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] Message::new 默认 from_bus = None
    #[test]
    fn message_new_defaults_from_bus_to_none() {
        let m = Message::new("x", NodeId::new("a"), vec![], serde_json::json!(null));
        assert!(m.from_bus.is_none());
    }

    // [构造] Message::with_from_bus 设置 from_bus
    #[test]
    fn message_with_from_bus_sets_field() {
        let bid = crate::node::BusId::new();
        let m = Message::with_from_bus("x", NodeId::new("a"), vec![], serde_json::json!(null), bid);
        assert_eq!(m.from_bus, Some(bid));
    }

    // [序列化] Message 含 from_bus 字段能 round-trip
    #[test]
    fn message_with_from_bus_serialization_roundtrip() {
        let m = Message::with_from_bus(
            "x",
            NodeId::new("a"),
            vec![],
            serde_json::json!(null),
            crate::node::BusId::new(),
        );
        let json = serde_json::to_string(&m).unwrap();
        let back: Message = serde_json::from_str(&json).unwrap();
        assert_eq!(back.from_bus, m.from_bus);
    }

    // [序列化] Message 无 from_bus 字段时反序列化得到 None（兼容旧数据）
    #[test]
    fn message_without_from_bus_deserializes_as_none() {
        // 历史数据格式：无 from_bus 字段
        let json = r#"{"id":"00000000-0000-0000-0000-000000000001","msg_type":"x","from":"a","to":[],"payload":null,"timestamp":0}"#;
        let m: Message = serde_json::from_str(json).unwrap();
        assert!(m.from_bus.is_none());
    }

    // [序列化] from_bus: None 时序列化输出不应包含字段
    #[test]
    fn message_without_from_bus_omits_field() {
        let m = Message::new("x", NodeId::new("a"), vec![], serde_json::json!(null));
        let json = serde_json::to_string(&m).unwrap();
        assert!(!json.contains("from_bus"));
    }

    // ═══════════════════════════════════════════════════════════════
    // NodeInfo — 6 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] 所有字段赋值后可读，值正确
    #[test]
    fn node_info_all_fields() {
        let info = NodeInfo {
            node_id: NodeId::new("mcp/filesystem"),
            node_type: "mcp".into(),
            capabilities: serde_json::json!({"resources": ["tool/read", "tool/write"]}),
            online_since: 1719500000000,
        };
        assert_eq!(info.node_id.as_str(), "mcp/filesystem");
        assert_eq!(info.node_type, "mcp");
        assert_eq!(info.online_since, 1719500000000);
    }

    // [序列化] Engine 类型 NodeInfo serde 往返：字段全等
    #[test]
    fn node_info_serialization_roundtrip() {
        let info = NodeInfo {
            node_id: NodeId::new("engine/session-1"),
            node_type: "engine".into(),
            capabilities: serde_json::json!({"sessions": ["sid-001"]}),
            online_since: 1700000000000,
        };
        let json = serde_json::to_string(&info).unwrap();
        let back: NodeInfo = serde_json::from_str(&json).unwrap();
        assert_eq!(info.node_id, back.node_id);
        assert_eq!(info.node_type, back.node_type);
        assert_eq!(info.capabilities, back.capabilities);
        assert_eq!(info.online_since, back.online_since);
    }

    // [类型] Engine 节点：capabilities 含 sessions 列表
    #[test]
    fn node_info_engine_type() {
        let info = NodeInfo {
            node_id: NodeId::new("engine/main"),
            node_type: "engine".into(),
            capabilities: serde_json::json!({"sessions": ["sid-abc"]}),
            online_since: 0,
        };
        let json = serde_json::to_string(&info).unwrap();
        assert!(json.contains("sessions"));
        assert!(json.contains("sid-abc"));
    }

    // [类型] Model 节点：capabilities 含 provider 和 models 列表
    #[test]
    fn node_info_model_type() {
        let info = NodeInfo {
            node_id: NodeId::new("model/openai"),
            node_type: "model".into(),
            capabilities: serde_json::json!({"provider": "openai", "models": ["gpt-4"]}),
            online_since: 0,
        };
        assert_eq!(info.node_type, "model");
    }

    // [类型] Trace 节点：capabilities 可为 null（无特殊能力声明）
    #[test]
    fn node_info_trace_type() {
        let info = NodeInfo {
            node_id: NodeId::new("trace/observer"),
            node_type: "trace".into(),
            capabilities: serde_json::Value::Null,
            online_since: 0,
        };
        assert_eq!(info.node_type, "trace");
    }

    // [边界] capabilities 为空对象 {}：序列化/反序列化后仍为 {}
    #[test]
    fn node_info_empty_capabilities() {
        let info = NodeInfo {
            node_id: NodeId::new("bare"),
            node_type: "engine".into(),
            capabilities: serde_json::json!({}),
            online_since: 0,
        };
        let json = serde_json::to_string(&info).unwrap();
        let back: NodeInfo = serde_json::from_str(&json).unwrap();
        assert_eq!(back.capabilities, serde_json::json!({}));
    }

    // ═══════════════════════════════════════════════════════════════
    // BusGraph — 3 tests
    // ═══════════════════════════════════════════════════════════════

    // [边界] 空图：nodes 为空 Vec，message_count=0，uptime_ms=0
    #[test]
    fn bus_graph_empty() {
        let graph = BusGraph {
            nodes: vec![],
            message_count: 0,
            uptime_ms: 0,
        };
        assert!(graph.nodes.is_empty());
        assert_eq!(graph.message_count, 0);
    }

    // [构造] 含多节点的图：node 列表长度和 message_count 正确
    #[test]
    fn bus_graph_with_nodes() {
        let graph = BusGraph {
            nodes: vec![
                NodeInfo {
                    node_id: NodeId::new("n1"),
                    node_type: "engine".into(),
                    capabilities: serde_json::Value::Null,
                    online_since: 1000,
                },
                NodeInfo {
                    node_id: NodeId::new("n2"),
                    node_type: "mcp".into(),
                    capabilities: serde_json::Value::Null,
                    online_since: 2000,
                },
            ],
            message_count: 42,
            uptime_ms: 5000,
        };
        assert_eq!(graph.nodes.len(), 2);
        assert_eq!(graph.message_count, 42);
    }

    // [序列化] BusGraph serde 往返：nodes 数量、计数、运行时间正确
    #[test]
    fn bus_graph_serialization_roundtrip() {
        let graph = BusGraph {
            nodes: vec![NodeInfo {
                node_id: NodeId::new("n1"),
                node_type: "mcp".into(),
                capabilities: serde_json::json!({"resources": ["tool/x"]}),
                online_since: 1,
            }],
            message_count: 10,
            uptime_ms: 100,
        };
        let json = serde_json::to_string(&graph).unwrap();
        let back: BusGraph = serde_json::from_str(&json).unwrap();
        assert_eq!(back.nodes.len(), 1);
        assert_eq!(back.message_count, 10);
        assert_eq!(back.uptime_ms, 100);
    }

    // ═══════════════════════════════════════════════════════════════
    // SendReceipt — 3 tests
    // ═══════════════════════════════════════════════════════════════

    // [边界] 零节点：online_nodes=0、matching_nodes=0（无人监听）
    #[test]
    fn send_receipt_zero_online_nodes() {
        let receipt = SendReceipt {
            message_id: uuid::Uuid::nil(),
            online_nodes: 0,
            matching_nodes: 0,
        };
        assert_eq!(receipt.online_nodes, 0);
        assert_eq!(receipt.matching_nodes, 0);
    }

    // [构造] online_nodes > matching_nodes 正常（部分节点 filter 不匹配）
    #[test]
    fn send_receipt_nonzero() {
        let id = uuid::Uuid::new_v4();
        let receipt = SendReceipt {
            message_id: id,
            online_nodes: 5,
            matching_nodes: 3,
        };
        assert_eq!(receipt.message_id, id);
        assert_eq!(receipt.online_nodes, 5);
        assert_eq!(receipt.matching_nodes, 3);
    }

    // [trait] Clone + PartialEq：克隆后字段一致
    #[test]
    fn send_receipt_clone() {
        let receipt = SendReceipt {
            message_id: uuid::Uuid::new_v4(),
            online_nodes: 2,
            matching_nodes: 2,
        };
        let cloned = receipt.clone();
        assert_eq!(receipt.message_id, cloned.message_id);
        assert_eq!(receipt.online_nodes, cloned.online_nodes);
    }

    // ═══════════════════════════════════════════════════════════════
    // SendError — 5 tests
    // ═══════════════════════════════════════════════════════════════

    // [trait] Display：NodeOffline 变体带节点列表，输出格式正确
    #[test]
    fn send_error_node_offline() {
        let e = SendError::NodeOffline(vec![NodeId::new("mcp/filesystem")]);
        assert_eq!(format!("{e}"), "target nodes offline: mcp/filesystem");
    }

    // [trait] Display：BusClosed 输出固定文本
    #[test]
    fn send_error_bus_closed() {
        assert_eq!(format!("{}", SendError::BusClosed), "bus closed");
    }

    // [trait] std::error::Error：SendError 满足 trait 约束，可被 anyhow/eyre 等消费
    #[test]
    fn send_error_implements_std_error() {
        fn takes_error(_e: impl std::error::Error) {}
        takes_error(SendError::BusClosed);
    }

    // [trait] Debug：{:?} 输出包含变体名
    #[test]
    fn send_error_debug() {
        let e = SendError::BusClosed;
        let debug = format!("{e:?}");
        assert!(debug.contains("BusClosed"));
    }

    // ═══════════════════════════════════════════════════════════════
    // MessageFilter & ToMatch — 8 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] types=None 表示不过滤 type，全收（Trace 节点行为）
    #[test]
    fn filter_types_none() {
        let f = MessageFilter {
            types: None,
            to_match: ToMatch::All,
        };
        assert!(f.types.is_none());
    }

    // [边界] types=Some(空数组) 表示显式"什么都不匹配"（静默节点用）
    #[test]
    fn filter_types_empty_vec() {
        let f = MessageFilter {
            types: Some(vec![]),
            to_match: ToMatch::BroadcastOnly,
        };
        assert_eq!(f.types.as_ref().unwrap().len(), 0);
    }

    // [构造] types=Some(单个 type)：白名单只含一种消息类型
    #[test]
    fn filter_types_single() {
        let f = MessageFilter {
            types: Some(vec!["heartbeat_request".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        assert_eq!(f.types.as_ref().unwrap().len(), 1);
    }

    // [构造] types=Some(多个 type)：白名单含多种消息类型（Engine 节点行为）
    #[test]
    fn filter_types_multiple() {
        let f = MessageFilter {
            types: Some(vec![
                "model_response".into(),
                "heartbeat_request".into(),
                "node_online".into(),
                "node_offline".into(),
            ]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        assert_eq!(f.types.as_ref().unwrap().len(), 4);
    }

    // [覆盖] ToMatch 四种变体均可构造（编译期验证枚举完整）
    #[test]
    fn to_match_all_variants_exist() {
        let _ = ToMatch::All;
        let _ = ToMatch::BroadcastOnly;
        let _ = ToMatch::DirectedToMe;
        let _ = ToMatch::BroadcastAndDirectedToMe;
    }

    // [trait] Clone + PartialEq：ToMatch 克隆后相等
    #[test]
    fn to_match_clone_and_eq() {
        let a = ToMatch::All;
        let b = a.clone();
        assert_eq!(a, b);
    }

    // [trait] MessageFilter Clone：types 和 to_match 克隆后一致
    #[test]
    fn message_filter_clone() {
        let f = MessageFilter {
            types: Some(vec!["t".into()]),
            to_match: ToMatch::DirectedToMe,
        };
        let cloned = f.clone();
        assert_eq!(f.types, cloned.types);
    }

    // ═══════════════════════════════════════════════════════════════
    // TaskId — 8 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] new() 自动生成非 nil UUID，owner 正确赋值
    #[test]
    fn task_id_new_fills_id_and_owner() {
        let owner = NodeId::new("engine/main");
        let tid = TaskId::new(owner.clone());
        assert!(!tid.id.is_nil());
        assert_eq!(tid.owner, owner);
    }

    // [唯一性] 连续两次 new() 生成的 id 不同（UUID v4 随机性）
    #[test]
    fn task_id_unique_each_call() {
        let owner = NodeId::new("engine/main");
        let a = TaskId::new(owner.clone());
        let b = TaskId::new(owner.clone());
        assert_ne!(a.id, b.id);
    }

    // [trait] Display：格式为 "uuid@owner"
    #[test]
    fn task_id_display_format() {
        let owner = NodeId::new("engine/main");
        let tid = TaskId::new(owner.clone());
        let display = format!("{tid}");
        assert!(display.ends_with("@engine/main"));
        assert!(display.contains('-'), "should contain UUID dashes");
    }

    // [边界] owner 为空字符串：不 panic，Display 输出 "@"
    #[test]
    fn task_id_empty_owner() {
        let owner = NodeId::new("");
        let tid = TaskId::new(owner);
        let display = format!("{tid}");
        assert!(display.ends_with('@'));
    }

    // [trait] Eq：相同 id+owner 相等，不同 id 不等
    #[test]
    fn task_id_equality() {
        let owner = NodeId::new("a");
        let a = TaskId::new(owner.clone());
        let b = TaskId::new(owner.clone());
        assert_eq!(a, a); // 同一个
        assert_ne!(a, b); // 不同 UUID
    }

    // [trait] Clone：克隆后与原值相等
    #[test]
    fn task_id_clone_is_equal() {
        let tid = TaskId::new(NodeId::new("x"));
        let cloned = tid.clone();
        assert_eq!(tid, cloned);
    }

    // [trait] Hash：TaskId 可作为 HashMap key
    #[test]
    fn task_id_hashable() {
        let mut map = std::collections::HashMap::new();
        let tid = TaskId::new(NodeId::new("engine/main"));
        map.insert(tid.clone(), 42);
        assert_eq!(map.get(&tid), Some(&42));
        // 不同 TaskId 不应命中
        let other = TaskId::new(NodeId::new("engine/main"));
        assert_eq!(map.get(&other), None);
    }

    // [序列化] serde 往返：id + owner 一致性
    #[test]
    fn task_id_serialization_roundtrip() {
        let tid = TaskId::new(NodeId::new("engine/main"));
        let json = serde_json::to_string(&tid).unwrap();
        let back: TaskId = serde_json::from_str(&json).unwrap();
        assert_eq!(tid, back);
        // 验证 JSON 结构含 id 和 owner 字段
        assert!(json.contains("\"id\""));
        assert!(json.contains("\"owner\""));
    }

    // ═══════════════════════════════════════════════════════════════
    // TaskStatus — 9 tests
    // ═══════════════════════════════════════════════════════════════

    // [覆盖] 6 种变体均可构造（编译期验证枚举完整）
    #[test]
    fn task_status_all_variants_construct() {
        let _ = TaskStatus::Created;
        let _ = TaskStatus::InProgress;
        let _ = TaskStatus::Blocked;
        let _ = TaskStatus::Resolved;
        let _ = TaskStatus::Failed;
        let _ = TaskStatus::Cancelled;
    }

    // [方法] is_terminal() — Created 不是终态
    #[test]
    fn task_status_created_is_not_terminal() {
        assert!(!TaskStatus::Created.is_terminal());
    }

    // [方法] is_terminal() — InProgress 不是终态
    #[test]
    fn task_status_in_progress_is_not_terminal() {
        assert!(!TaskStatus::InProgress.is_terminal());
    }

    // [方法] is_terminal() — Blocked 不是终态
    #[test]
    fn task_status_blocked_is_not_terminal() {
        assert!(!TaskStatus::Blocked.is_terminal());
    }

    // [方法] is_terminal() — Resolved / Failed / Cancelled 是终态
    #[test]
    fn task_status_terminal_states() {
        assert!(TaskStatus::Resolved.is_terminal());
        assert!(TaskStatus::Failed.is_terminal());
        assert!(TaskStatus::Cancelled.is_terminal());
    }

    // [trait] PartialEq：相同变体相等，不同不等
    #[test]
    fn task_status_equality() {
        assert_eq!(TaskStatus::Created, TaskStatus::Created);
        assert_ne!(TaskStatus::Created, TaskStatus::InProgress);
        assert_eq!(TaskStatus::Resolved, TaskStatus::Resolved);
        assert_ne!(TaskStatus::Failed, TaskStatus::Cancelled);
    }

    // [trait] Display：每个变体输出 snake_case 小写
    #[test]
    fn task_status_display() {
        assert_eq!(format!("{}", TaskStatus::Created), "created");
        assert_eq!(format!("{}", TaskStatus::InProgress), "in_progress");
        assert_eq!(format!("{}", TaskStatus::Blocked), "blocked");
        assert_eq!(format!("{}", TaskStatus::Resolved), "resolved");
        assert_eq!(format!("{}", TaskStatus::Failed), "failed");
        assert_eq!(format!("{}", TaskStatus::Cancelled), "cancelled");
    }

    // [trait] Clone：克隆后与原值相等
    #[test]
    fn task_status_clone() {
        let s = TaskStatus::InProgress;
        assert_eq!(s, s.clone());
    }

    // [序列化] 每个变体 serde 往返：JSON 字符串 ↔ enum 一致
    #[test]
    fn task_status_serialization_roundtrip() {
        for status in [
            TaskStatus::Created,
            TaskStatus::InProgress,
            TaskStatus::Blocked,
            TaskStatus::Resolved,
            TaskStatus::Failed,
            TaskStatus::Cancelled,
        ] {
            let json = serde_json::to_string(&status).unwrap();
            let back: TaskStatus = serde_json::from_str(&json).unwrap();
            assert_eq!(status, back);
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // ModelMessage — 15 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] new() 正确赋值 role 和 content，可选字段默认为 None/Null
    #[test]
    fn model_message_new_sets_fields() {
        let msg = ModelMessage::new("user", "hello world");
        assert_eq!(msg.role, "user");
        assert_eq!(msg.content, "hello world");
        assert_eq!(msg.tool_call_id, None);
        assert_eq!(msg.name, None);
        assert_eq!(msg.extra, serde_json::Value::Null);
    }

    // [构造] new() 接受 owned String（Into<String> trait）
    #[test]
    fn model_message_new_from_string() {
        let role = String::from("assistant");
        let content = String::from("response");
        let msg = ModelMessage::new(role, content);
        assert_eq!(msg.role, "assistant");
        assert_eq!(msg.content, "response");
    }

    // [构造] builder: with_tool_call_id() 设置 tool_call_id
    #[test]
    fn model_message_with_tool_call_id() {
        let msg = ModelMessage::new("tool", "result").with_tool_call_id("call_abc123");
        assert_eq!(msg.tool_call_id, Some("call_abc123".into()));
        assert_eq!(msg.role, "tool");
    }

    // [构造] builder: with_name() 设置 name
    #[test]
    fn model_message_with_name() {
        let msg = ModelMessage::new("tool", "result").with_name("read_file");
        assert_eq!(msg.name, Some("read_file".into()));
    }

    // [构造] builder: 链式调用同时设置三个可选字段
    #[test]
    fn model_message_builder_chained() {
        let msg = ModelMessage::new("tool", r#"{"status": "ok"}"#)
            .with_tool_call_id("call_xyz")
            .with_name("search")
            .with_extra(serde_json::json!({"reasoning_content": "let me think..."}));
        assert_eq!(msg.tool_call_id, Some("call_xyz".into()));
        assert_eq!(msg.name, Some("search".into()));
        assert_eq!(msg.extra["reasoning_content"], "let me think...");
    }

    // [边界] role 为空字符串：不 panic
    #[test]
    fn model_message_empty_role() {
        let msg = ModelMessage::new("", "content");
        assert_eq!(msg.role, "");
    }

    // [边界] content 为空字符串：不 panic
    #[test]
    fn model_message_empty_content() {
        let msg = ModelMessage::new("system", "");
        assert_eq!(msg.role, "system");
        assert_eq!(msg.content, "");
    }

    // [trait] PartialEq：相同字段相等，不同不等（含可选字段）
    #[test]
    fn model_message_equality() {
        let a = ModelMessage::new("user", "hello");
        let b = ModelMessage::new("user", "hello");
        let c = ModelMessage::new("user", "world");
        let d = ModelMessage::new("tool", "hello").with_tool_call_id("id1");
        let e = ModelMessage::new("tool", "hello").with_tool_call_id("id1");
        let f = ModelMessage::new("tool", "hello").with_tool_call_id("id2");
        assert_eq!(a, b);
        assert_ne!(a, c);
        assert_eq!(d, e);
        assert_ne!(d, f);
    }

    // [trait] Clone：克隆后与原值相等（含可选字段）
    #[test]
    fn model_message_clone() {
        let msg = ModelMessage::new("tool", "reply")
            .with_tool_call_id("call_1")
            .with_name("run")
            .with_extra(serde_json::json!({"key": "value"}));
        assert_eq!(msg, msg.clone());
    }

    // [序列化] user 消息：可选字段为 None/Null 时不输出到 JSON
    #[test]
    fn model_message_serialization_user_skips_optionals() {
        let msg = ModelMessage::new("user", "hello");
        let json = serde_json::to_string(&msg).unwrap();
        let back: ModelMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, back);
        assert!(!json.contains("tool_call_id"));
        assert!(!json.contains("\"name\""));
        assert!(!json.contains("\"extra\""));
    }

    // [序列化] tool 消息：可选字段有值时输出到 JSON
    #[test]
    fn model_message_serialization_tool_with_options() {
        let msg = ModelMessage::new("tool", r#"{"result": "ok"}"#)
            .with_tool_call_id("call_abc")
            .with_name("search");
        let json = serde_json::to_string(&msg).unwrap();
        let back: ModelMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, back);
        assert!(json.contains("tool_call_id"));
        assert!(json.contains("call_abc"));
        assert!(json.contains("\"name\""));
        assert!(json.contains("search"));
    }

    // [序列化] tool 消息带 extra：extra 非 null 时输出到 JSON
    #[test]
    fn model_message_serialization_with_extra() {
        let msg = ModelMessage::new("assistant", "answer")
            .with_extra(serde_json::json!({"reasoning_content": "step 1: ..."}));
        let json = serde_json::to_string(&msg).unwrap();
        let back: ModelMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, back);
        assert!(json.contains("\"extra\""));
        assert!(json.contains("reasoning_content"));
    }

    // [兼容] 旧数据无 extra 字段：反序列化不报错，extra 为 Null
    #[test]
    fn model_message_deserialize_missing_extra() {
        let json = r#"{"role":"user","content":"hello"}"#;
        let msg: ModelMessage = serde_json::from_str(json).unwrap();
        assert_eq!(msg.role, "user");
        assert_eq!(msg.extra, serde_json::Value::Null);
    }

    // [边界] tool_call_id 为空字符串：序列化往返后仍为空字符串
    #[test]
    fn model_message_tool_call_id_empty_string() {
        let msg = ModelMessage::new("tool", "x").with_tool_call_id("");
        let json = serde_json::to_string(&msg).unwrap();
        let back: ModelMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(back.tool_call_id, Some("".into()));
    }

    // [序列化] extra 嵌套对象（2层 + 数组 + null）：结构不丢失
    #[test]
    fn model_message_extra_deeply_nested() {
        let extra = serde_json::json!({
            "reasoning_content": "think",
            "meta": {"tokens": 42, "tags": ["a", null, "b"]}
        });
        let msg = ModelMessage::new("assistant", "ok").with_extra(extra);
        let json = serde_json::to_string(&msg).unwrap();
        let back: ModelMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(msg.extra, back.extra);
        assert_eq!(back.extra["meta"]["tokens"], 42);
        assert_eq!(back.extra["meta"]["tags"][1], serde_json::Value::Null);
    }
}
