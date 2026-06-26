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
- `payload: serde_json::Value` — 任意 JSON，Engine 塞模型输出，MCP 塞工具结果，不做 schema 约束
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
    pub node_type: String,
    /// Capabilities: `{"tools": [...], "session_id": "s1"}`.
    pub capabilities: serde_json::Value,
    /// Milliseconds since Unix epoch when this node connected.
    pub online_since: u64,
}
```

逐行：
- `capabilities: Value` — 不同 node_type 声明不同能力，MCP 声明 tool 列表，Engine 声明 session_id，不做强类型约束保持灵活
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
#[derive(Debug, Clone)]
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
#[derive(Debug, Clone)]
pub struct MessageFilter {
    /// If set, only messages of these types pass through.
    /// `None` means accept all types.
    pub types: Option<Vec<String>>,
    /// How to handle `to`-based filtering.
    pub to_match: ToMatch,
}

/// How a node's filter matches the `to` field on messages.
#[derive(Debug, Clone)]
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

替换占位测试为验证类型可用性的测试：

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn node_id_creation_and_display() {
        let id = NodeId::new("engine/main");
        assert_eq!(id.as_str(), "engine/main");
        assert_eq!(format!("{id}"), "engine/main");
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
    fn message_new_fills_id_and_timestamp() {
        let from = NodeId::new("test");
        let msg = Message::new("test_type", from.clone(), None, serde_json::json!({"k": "v"}));
        assert!(!msg.id.is_nil());
        assert!(msg.timestamp > 0);
        assert_eq!(msg.from, from);
        assert!(msg.is_broadcast());
    }

    #[test]
    fn message_is_for_directed() {
        let a = NodeId::new("a");
        let b = NodeId::new("b");
        let msg = Message::new("t", a.clone(), Some(b.clone()), serde_json::json!(null));
        assert!(msg.is_for(&b));
        assert!(!msg.is_for(&a));
        assert!(!msg.is_broadcast());
    }

    #[test]
    fn message_serialization_roundtrip() {
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
    }

    #[test]
    fn node_info_serialization() {
        let info = NodeInfo {
            node_id: NodeId::new("mcp/fs"),
            node_type: "mcp".into(),
            capabilities: serde_json::json!({"tools": ["read", "write"]}),
            online_since: 1000,
        };
        let json = serde_json::to_string(&info).unwrap();
        assert!(json.contains("mcp/fs"));
        assert!(json.contains("tools"));
    }

    #[test]
    fn send_error_display() {
        assert_eq!(
            format!("{}", SendError::NodeOffline(NodeId::new("x"))),
            "target node offline: x"
        );
        assert_eq!(format!("{}", SendError::BusFull), "bus buffer full");
        assert_eq!(format!("{}", SendError::BusClosed), "bus closed");
    }

    #[test]
    fn message_filter_construction() {
        let f = MessageFilter {
            types: Some(vec!["model_response".into(), "heartbeat_request".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        assert_eq!(f.types.unwrap().len(), 2);
    }
}
```

---

## 小结

- **8 个类型 + 1 个枚举**，全部在 `arf-core` 中
- **newtype 模式**保护类型安全（`NodeId` 不与 `String` 混淆）
- **serde 全覆盖**，消息可 JSON 序列化/反序列化
- **`to` 字段**是接收侧过滤的 hint，Bus 不做路由决策
- 测试覆盖构造、比较、序列化、`Display`
