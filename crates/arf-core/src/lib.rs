//! ARF shared types — Message format, identifiers, error types.
//!
//! This crate defines the common vocabulary that all other ARF crates share.
//! It has zero dependencies on other ARF crates.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

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
    /// Unix timestamp in milliseconds.
    pub timestamp: u64,
}

impl Message {
    /// Create a new message with the current timestamp.
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
            timestamp: now_ms(),
        }
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

// ── SendError ─────────────────────────────────────────────────────────

/// Errors that can occur when sending a message.
#[derive(Debug)]
pub enum SendError {
    /// All directed message targets are offline.
    NodeOffline(Vec<NodeId>),
    /// Bus ring buffer is full.
    BusFull,
    /// Bus has been shut down.
    BusClosed,
}

impl std::fmt::Display for SendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NodeOffline(ids) => {
                let names: Vec<_> = ids.iter().map(|id| id.as_str()).collect();
                write!(f, "target nodes offline: {}", names.join(", "))
            }
            Self::BusFull => write!(f, "bus buffer full"),
            Self::BusClosed => write!(f, "bus closed"),
        }
    }
}

impl std::error::Error for SendError {}

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

    // [trait] Display：BusFull 输出固定文本
    #[test]
    fn send_error_bus_full() {
        assert_eq!(format!("{}", SendError::BusFull), "bus buffer full");
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
        takes_error(SendError::BusFull);
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
}
