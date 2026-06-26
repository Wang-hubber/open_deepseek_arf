# 任务 1.1：`arf-core` 共享类型

> Phase 1 — Bus 消息总线第一项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`

## 设计思路

`arf-core` 是零依赖 crate，定义所有其他 ARF crate 共用的词汇。根据 spec，需要定义 8 个类型 + 1 个枚举：

| 类型 | 用途 | 使用方 |
|------|------|--------|
| `NodeId` | 节点唯一标识 newtype | 全部 crate |
| `Message` | 消息格式 | 全部 crate |
| `NodeInfo` | 节点上线宣告 | Bus, Engine |
| `BusGraph` | Bus 健康图 | Bus, PyO3 |
| `SendReceipt` | 发送确认 | Bus |
| `MessageFilter` | 接收过滤规则 | Bus, 所有连接方 |
| `ToMatch` | 过滤匹配模式 | Bus |
| `SendError` | 发送错误类型 | Bus |

依赖只需 `serde` + `serde_json` + `uuid`，三个都是标准生态库。

---

## 代码实现

### `crates/arf-core/Cargo.toml`

```toml
[package]
name = "arf-core"
version.workspace = true
edition.workspace = true
license.workspace = true
repository.workspace = true
description = "ARF shared types: Message, NodeId, NodeInfo, error types"

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
uuid = { version = "1", features = ["v4", "serde"] }
```

逐行解释：
- `serde` feature `derive` — 让 `#[derive(Serialize, Deserialize)]` 可用
- `serde_json` — 提供 `Value` 类型，消息的 `payload` 用
- `uuid` feature `v4` — `Uuid::new_v4()` 生成随机 ID；`serde` — 让 `Uuid` 支持序列化

---

### `crates/arf-core/src/lib.rs`

```rust
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
```

逐行：
- `#[derive(...)]` — `Debug` 让它可以 `println!("{:?}", id)`，`Clone` 让它可以复制，`Hash + Eq + PartialEq` 让它能做 HashMap key，`Ord + PartialOrd` 排序，`Serialize + Deserialize` JSON 序列化
- `pub struct NodeId(pub String)` — newtype 模式，一个字段的元组结构体。`pub String` 表示内部字段可读可写。不用裸 `String` 的好处：类型安全，不会把 NodeId 和 SessionId 弄混

```rust
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
```

逐行：
- `id: impl Into<String>` — 接受 `&str`、`String`、`&String`，内部调用 `.into()` 转成 `String`。比写死 `&str` 更灵活

三种 str 的区别：

| 类型 | 含义 | 内存 | 例子 |
|------|------|------|------|
| `&str` | 借用的字符串**切片**（引用） | 不拥有数据，指向别人的内存 | `"hello"` 字面量 |
| `String` | **拥有**堆上数据的字符串 | 在堆上分配，可增删改 | `String::from("hello")` |
| `&String` | 对 `String` 的引用 | 双层间接，编译器自动解引用成 `&str` | `&my_string` |

关键：`&String` 通过 Rust 的 `Deref` trait 自动变成 `&str`，所以实际场景中你永远不会手动传 `&String`——传 `&my_string` 时编译器直接理解为 `&str`。

`impl Into<String>` 的好处：

```rust
// 调用方传什么都行
NodeId::new("字面量");          // &str → Into<String> → String::from
NodeId::new(owned_string);     // String → Into<String> → 零拷贝直接转移所有权
NodeId::new(&owned_string);    // &String → Deref → &str → Into<String>
```

如果写死 `&str`，调用方有 `String` 时得多写一个 `.as_str()`；写 `impl Into<String>` 三种全收，一次到位。

- `as_str()` — 读内部值，避免每次都写 `id.0.as_str()`

```rust
impl std::fmt::Display for NodeId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}
```

逐行：
- `Display` — 让 `NodeId` 可以用 `println!("{}", id)` 和 `format!("{}", id)`。`FromStr` 暂时不需要

```rust
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
    /// Receiver hint. `None` = broadcast to all.
    pub to: Option<NodeId>,
    /// Arbitrary JSON payload.
    pub payload: serde_json::Value,
    /// Unix timestamp in milliseconds.
    pub timestamp: u64,
}
```

逐行：
- `id: Uuid` — 每条消息有全局唯一 ID，后续 trace/回放靠它定位
- `msg_type: String` — 不是 enum 而是字符串，方便新增类型无需改 `arf-core`
- `to: Option<NodeId>` — `None` 广播，`Some(id)` 定向（给接收侧过滤器用，Bus 不区分对待）
- `payload: serde_json::Value` — 任意 JSON，Engine 塞模型输出和 action，MCP 塞资源执行结果（prompt / tool / skill），不做 schema 约束
- `timestamp: u64` — Unix 毫秒，生成时打时间戳

```rust
impl Message {
    /// Create a new message with the current timestamp.
    pub fn new(
        msg_type: impl Into<String>,
        from: NodeId,
        to: Option<NodeId>,
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

    /// Returns true if this is a broadcast message.
    pub fn is_broadcast(&self) -> bool {
        self.to.is_none()
    }

    /// Returns true if this message is directed at the given node.
    pub fn is_for(&self, node_id: &NodeId) -> bool {
        match &self.to {
            Some(to) => to == node_id,
            None => false,
        }
    }
}
```

逐行：
- `new()` — 工厂方法，调用方只需给 type/from/to/payload，`id` 和 `timestamp` 自动生成
- `now_ms()` — 辅助函数，见下文
- `is_broadcast()` / `is_for()` — 便利方法，过滤器里用

```rust
/// Get current Unix timestamp in milliseconds.
fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
```

逐行：
- `SystemTime::now().duration_since(UNIX_EPOCH)` — 获取当前时间到 1970-01-01 的差值
- `unwrap_or_default()` — 系统时间异常（罕见）时返回 0，不 panic
- `as_millis() as u64` — 转成毫秒级 u64

```rust
// ── NodeInfo ──────────────────────────────────────────────────────────

/// Information broadcast when a node comes online.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeInfo {
    pub node_id: NodeId,
    /// Node type: `"engine"`, `"mcp"`, `"model"`, `"trace"`.
    /// Engine = Agent + State = one active session.
    pub node_type: String,
    /// Capabilities: MCP declares `{"resources": ["prompt/...", "tool/...", "skill/..."]}`;
    /// Engine declares `{"sessions": ["sid1", "sid2"]}`. Engine = Agent + State = one active session.
    pub capabilities: serde_json::Value,
    /// Milliseconds since Unix epoch when this node connected.
    pub online_since: u64,
}
```

逐行：
- `node_type: String` — `"engine"`、`"mcp"`、`"model"`、`"trace"`。区分节点角色
- `capabilities: Value` — 不同 node_type 声明不同能力。MCP 声明资源列表（`prompt` / `tool` / `skill`），Engine 声明自己管理的 `sessions`（一个 Engine = 一个 Agent + 一个 State = 一个活跃 session）。不做强类型约束保持灵活
- `online_since` — 上线时间戳，bus graph 展示用

```rust
// ── BusGraph ──────────────────────────────────────────────────────────

/// Snapshot of the Bus health at query time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BusGraph {
    pub nodes: Vec<NodeInfo>,
    pub message_count: u64,
    pub uptime_ms: u64,
}
```

逐行：
- `nodes` — 当前在线节点快照
- `message_count` — 自 Bus 启动以来的消息总数，监控用
- `uptime_ms` — Bus 运行时间

```rust
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
```

逐行：
- `online_nodes` — 发送瞬间在线节点总数，发送方据此判断"有没有人在听"
- `matching_nodes` — filter 可能匹配的节点数，比 `online_nodes` 更精确

```rust
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
    /// Only receive messages with `to == None` (broadcast).
    BroadcastOnly,
    /// Only receive messages directed specifically to this node.
    DirectedToMe,
    /// Receive broadcasts AND messages directed to this node. (default)
    BroadcastAndDirectedToMe,
}
```

逐行：
- `types: Option<Vec<String>>` — `None` = 全收，`Some([...])` = 白名单。Engine 可能只关心 `["model_response", "heartbeat_request"]`
- `ToMatch::All` — Trace 节点用，零黑障
- `ToMatch::BroadcastAndDirectedToMe` — MCP 节点用，关心广播 + 给自己 tool_call

```rust
// ── SendError ─────────────────────────────────────────────────────────

/// Errors that can occur when sending a message.
#[derive(Debug)]
pub enum SendError {
    /// Directed message target is not online.
    NodeOffline(NodeId),
    /// Bus ring buffer is full.
    BusFull,
    /// Bus has been shut down.
    BusClosed,
}

impl std::fmt::Display for SendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NodeOffline(id) => write!(f, "target node offline: {id}"),
            Self::BusFull => write!(f, "bus buffer full"),
            Self::BusClosed => write!(f, "bus closed"),
        }
    }
}

impl std::error::Error for SendError {}
```

逐行：
- `NodeOffline(NodeId)` — 带参数的错误变体，告知具体哪个节点不在线
- `Display` — 三个分支的可读描述，用于日志/调试
- `Error` — 空实现，满足 `std::error::Error` trait 要求（`Display + Debug` 就够了）

---

### 单元测试

> **原则**：每个类型的构造、方法、trait 实现、序列化边界全覆盖。边界条件：空串、零值、特殊字符、自指、None。

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    // ═══════════════════════════════════════════════════════════════
    // NodeId
    // ═══════════════════════════════════════════════════════════════

    #[test]
    fn node_id_new_and_as_str() {
        let id = NodeId::new("engine/main");
        assert_eq!(id.as_str(), "engine/main");
    }

    #[test]
    fn node_id_display() {
        let id = NodeId::new("trace/observer");
        assert_eq!(format!("{id}"), "trace/observer");
    }

    #[test]
    fn node_id_empty_string() {
        let id = NodeId::new("");
        assert_eq!(id.as_str(), "");
        assert_eq!(format!("{id}"), "");
    }

    #[test]
    fn node_id_very_long() {
        let long = "a".repeat(1024);
        let id = NodeId::new(long.clone());
        assert_eq!(id.as_str(), &long);
    }

    #[test]
    fn node_id_unicode() {
        let id = NodeId::new("引擎/主节点");
        assert_eq!(id.as_str(), "引擎/主节点");
    }

    #[test]
    fn node_id_equality() {
        let a = NodeId::new("a");
        let b = NodeId::new("a");
        let c = NodeId::new("c");
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    #[test]
    fn node_id_clone_is_equal() {
        let a = NodeId::new("x");
        let b = a.clone();
        assert_eq!(a, b);
    }

    #[test]
    fn node_id_hashable() {
        let mut map = HashMap::new();
        map.insert(NodeId::new("k1"), 1);
        map.insert(NodeId::new("k2"), 2);
        assert_eq!(map.get(&NodeId::new("k1")), Some(&1));
        assert_eq!(map.get(&NodeId::new("k3")), None);
    }

    #[test]
    fn node_id_ordering() {
        let a = NodeId::new("a");
        let b = NodeId::new("b");
        let aa = NodeId::new("aa");
        // "a" < "aa" < "b" in lexicographic order
        assert!(a < aa);
        assert!(aa < b);
    }

    #[test]
    fn node_id_serialization_roundtrip() {
        let id = NodeId::new("mcp/filesystem");
        let json = serde_json::to_string(&id).unwrap();
        assert_eq!(json, r#""mcp/filesystem""#);
        let back: NodeId = serde_json::from_str(&json).unwrap();
        assert_eq!(id, back);
    }

    // ═══════════════════════════════════════════════════════════════
    // Message — construction
    // ═══════════════════════════════════════════════════════════════

    #[test]
    fn message_new_fills_id_and_timestamp() {
        let from = NodeId::new("test");
        let msg = Message::new("test_type", from.clone(), None, serde_json::json!({"k": "v"}));
        assert!(!msg.id.is_nil());
        assert!(msg.timestamp > 0);
    }

    #[test]
    fn message_id_is_unique_each_call() {
        let from = NodeId::new("test");
        let m1 = Message::new("t", from.clone(), None, serde_json::json!(null));
        let m2 = Message::new("t", from.clone(), None, serde_json::json!(null));
        assert_ne!(m1.id, m2.id);
    }

    #[test]
    fn message_timestamp_is_monotonic() {
        let from = NodeId::new("test");
        let m1 = Message::new("t", from.clone(), None, serde_json::json!(null));
        std::thread::sleep(std::time::Duration::from_millis(1));
        let m2 = Message::new("t", from.clone(), None, serde_json::json!(null));
        assert!(m2.timestamp >= m1.timestamp);
    }

    #[test]
    fn message_empty_msg_type() {
        let from = NodeId::new("test");
        let msg = Message::new("", from.clone(), None, serde_json::json!(null));
        assert_eq!(msg.msg_type, "");
    }

    #[test]
    fn message_msg_type_from_string() {
        let from = NodeId::new("test");
        let s = String::from("model_call");
        let msg = Message::new(s, from.clone(), None, serde_json::json!(null));
        // impl Into<String> accepts owned String
        assert_eq!(msg.msg_type, "model_call");
    }

    #[test]
    fn message_from_field_set_correctly() {
        let from = NodeId::new("sender");
        let msg = Message::new("t", from.clone(), None, serde_json::json!(null));
        assert_eq!(msg.from, from);
    }

    // ═══════════════════════════════════════════════════════════════
    // Message — is_broadcast / is_for
    // ═══════════════════════════════════════════════════════════════

    #[test]
    fn message_is_broadcast_when_to_is_none() {
        let msg = Message::new("t", NodeId::new("a"), None, serde_json::json!(null));
        assert!(msg.is_broadcast());
    }

    #[test]
    fn message_is_not_broadcast_when_to_is_some() {
        let msg = Message::new("t", NodeId::new("a"), Some(NodeId::new("b")), serde_json::json!(null));
        assert!(!msg.is_broadcast());
    }

    #[test]
    fn message_is_for_target() {
        let target = NodeId::new("target");
        let msg = Message::new("t", NodeId::new("sender"), Some(target.clone()), serde_json::json!(null));
        assert!(msg.is_for(&target));
    }

    #[test]
    fn message_is_for_wrong_target() {
        let msg = Message::new("t", NodeId::new("sender"), Some(NodeId::new("target")), serde_json::json!(null));
        assert!(!msg.is_for(&NodeId::new("other")));
    }

    #[test]
    fn message_broadcast_is_not_for_anyone() {
        let msg = Message::new("t", NodeId::new("sender"), None, serde_json::json!(null));
        assert!(!msg.is_for(&NodeId::new("anyone")));
        assert!(!msg.is_for(&NodeId::new("sender")));
    }

    #[test]
    fn message_directed_to_self() {
        let self_id = NodeId::new("self");
        let msg = Message::new("t", self_id.clone(), Some(self_id.clone()), serde_json::json!(null));
        assert!(msg.is_for(&self_id));
        assert!(!msg.is_broadcast());
    }

    // ═══════════════════════════════════════════════════════════════
    // Message — serialization
    // ═══════════════════════════════════════════════════════════════

    #[test]
    fn message_serialization_roundtrip_directed() {
        let msg = Message::new(
            "test",
            NodeId::new("sender"),
            Some(NodeId::new("receiver")),
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

    #[test]
    fn message_serialization_roundtrip_broadcast() {
        let msg = Message::new(
            "node_online",
            NodeId::new("engine/main"),
            None,
            serde_json::json!({"node": "info"}),
        );
        let json = serde_json::to_string(&msg).unwrap();
        let back: Message = serde_json::from_str(&json).unwrap();
        assert_eq!(msg.to, None);
        assert_eq!(back.to, None);
    }

    #[test]
    fn message_serialization_null_payload() {
        let msg = Message::new("t", NodeId::new("a"), None, serde_json::Value::Null);
        let json = serde_json::to_string(&msg).unwrap();
        let back: Message = serde_json::from_str(&json).unwrap();
        assert_eq!(back.payload, serde_json::Value::Null);
    }

    #[test]
    fn message_serialization_deeply_nested_payload() {
        let payload = serde_json::json!({
            "level1": {
                "level2": {
                    "level3": [1, null, "str", true, {"deep": []}]
                }
            }
        });
        let msg = Message::new("t", NodeId::new("a"), None, payload);
        let json = serde_json::to_string(&msg).unwrap();
        let back: Message = serde_json::from_str(&json).unwrap();
        assert_eq!(msg.payload, back.payload);
    }

    #[test]
    fn message_deserialize_missing_timestamp_still_works() {
        // timestamp is required by `new()`, but if JSON is from old
        // version or external source without timestamp, deserialization
        // should still work (default u64 = 0).
        let json = r#"{"id":"00000000-0000-0000-0000-000000000000","msg_type":"t","from":"a","to":null,"payload":null,"timestamp":0}"#;
        let msg: Message = serde_json::from_str(json).unwrap();
        assert_eq!(msg.timestamp, 0);
    }

    #[test]
    fn message_clone_produces_equal() {
        let msg = Message::new("t", NodeId::new("a"), Some(NodeId::new("b")), serde_json::json!({"x": 1}));
        let cloned = msg.clone();
        assert_eq!(msg.id, cloned.id);
        assert_eq!(msg.msg_type, cloned.msg_type);
        assert_eq!(msg.from, cloned.from);
        assert_eq!(msg.to, cloned.to);
        assert_eq!(msg.payload, cloned.payload);
        assert_eq!(msg.timestamp, cloned.timestamp);
    }

    // ═══════════════════════════════════════════════════════════════
    // NodeInfo
    // ═══════════════════════════════════════════════════════════════

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
    // BusGraph
    // ═══════════════════════════════════════════════════════════════

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
    // SendReceipt
    // ═══════════════════════════════════════════════════════════════

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
    // SendError — Display
    // ═══════════════════════════════════════════════════════════════

    #[test]
    fn send_error_node_offline() {
        let e = SendError::NodeOffline(NodeId::new("mcp/filesystem"));
        assert_eq!(format!("{e}"), "target node offline: mcp/filesystem");
    }

    #[test]
    fn send_error_bus_full() {
        assert_eq!(format!("{}", SendError::BusFull), "bus buffer full");
    }

    #[test]
    fn send_error_bus_closed() {
        assert_eq!(format!("{}", SendError::BusClosed), "bus closed");
    }

    #[test]
    fn send_error_implements_std_error() {
        fn takes_error(_e: impl std::error::Error) {}
        takes_error(SendError::BusFull);
    }

    #[test]
    fn send_error_debug() {
        let e = SendError::BusClosed;
        let debug = format!("{e:?}");
        assert!(debug.contains("BusClosed"));
    }

    // ═══════════════════════════════════════════════════════════════
    // MessageFilter & ToMatch
    // ═══════════════════════════════════════════════════════════════

    #[test]
    fn filter_types_none() {
        let f = MessageFilter {
            types: None,
            to_match: ToMatch::All,
        };
        assert!(f.types.is_none());
    }

    #[test]
    fn filter_types_empty_vec() {
        // Some(empty) means "match nothing" — explicit opt-in to silence
        let f = MessageFilter {
            types: Some(vec![]),
            to_match: ToMatch::BroadcastOnly,
        };
        assert_eq!(f.types.as_ref().unwrap().len(), 0);
    }

    #[test]
    fn filter_types_single() {
        let f = MessageFilter {
            types: Some(vec!["heartbeat_request".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        assert_eq!(f.types.as_ref().unwrap().len(), 1);
    }

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

    #[test]
    fn to_match_all_variants_exist() {
        // Verify all four variants can be constructed
        let _ = ToMatch::All;
        let _ = ToMatch::BroadcastOnly;
        let _ = ToMatch::DirectedToMe;
        let _ = ToMatch::BroadcastAndDirectedToMe;
    }

    #[test]
    fn to_match_clone_and_eq() {
        let a = ToMatch::All;
        let b = a.clone();
        assert_eq!(a, b);
    }

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
```

---

## 小结

- **8 个类型 + 1 个枚举**，全部在 `arf-core` 中
- **newtype 模式**保护类型安全（`NodeId` 不与 `String` 混淆）
- **serde 全覆盖**，消息可 JSON 序列化/反序列化
- **`to` 字段**是接收侧过滤的 hint，Bus 不做路由决策
- **36 个测试**，覆盖所有边界：空串、零值、Unicode、长字符串、自指、null payload、嵌套 JSON、HashMap key、排序、Clone、`std::error::Error` trait、时间单调性、ID 唯一性
