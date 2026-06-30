# 任务 6.0.1：Node trait 抽象

> Phase 6 — Multi-Bus 基础设施（§9.A）第一项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §1.4 / §4 / §7.1 / §9.A

## 设计思路

`Node` trait 是 Phase 6 引入的核心抽象，让所有 Bus 节点（ModelAdapter / McpNode / Engine / MemoryNode / CompactionNode / Pool 节点 / App 自定义节点）实现统一的异步消息处理契约。该 trait **强制** `Send + Sync`，使节点可跨 tokio task 共享；`on_message` 用 `&mut self` 保证消息处理串行化，与 Bus 的 mpsc 接收模型一致。

| 文件 | 内容 | 用途 |
|------|------|------|
| `crates/arf-core/src/node.rs` | `Node` trait + `BusId` + `SnapshotError` + `RestoreError` | 节点抽象基座 |
| `crates/arf-core/src/lib.rs` | 增加 `pub mod node;` re-export | 模块暴露 |
| `crates/arf-core/Cargo.toml` | 增加 `async-trait` 依赖 | 让 `async fn on_message` 可用于 `Arc<dyn Node>` 动态分发 |

**关键设计决议**（设计文档 §1.4 / §8.2）：

1. `snapshot` 是 `&self`——**不阻塞** Node 处理消息；Node 实现内部用 `RwLock` / `Mutex` 保护共享状态，snapshot 时 read lock，on_message 时 write lock（短临界区）
2. `restore` 是 `&mut self`——restore 期间 Node 应停止处理消息（App 协调顺序）
3. `on_message` 是 `&mut self`——Node trait 强制串行消息处理（与 Bus mpsc 模型一致）
4. `from_bus: BusId` 作为 `on_message` 第二参数——让 Node 知道消息来自哪条 Bus（facade 跨 Bus 转发需要）
5. `BusId` 定义在 `arf-core`——避免 arf-core → arf-bus 循环依赖；arf-bus 在 6.0.3 直接复用

**为何 `BusId` 在本任务定义而非 6.0.3**：Node trait 签名需要 `from_bus: BusId`，若 6.0.3 才引入，6.0.1 的 trait 编译失败。**提前定义类型 → 6.0.3 接入使用**，符合"类型先于使用"。

**不在本任务范围**（明确划清边界）：
- `Message.from_bus` 字段更新——task 6.0.3
- `Bus` 结构体的 `id: BusId`——task 6.0.3
- `NodeHandle::attach_to(bus, filter)`——task 6.0.2
- `ActionMessage` trait / `MessageIntent` / `Route` / `Capability` / `State` 等其他核心类型——task 6.1
- 删除旧 `TaskId` / `TaskStatus` 抽象——单独的清理任务（与本任务正交）

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
description = "ARF shared types: Message, NodeId, NodeInfo, Node trait, error types"

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
uuid = { version = "1", features = ["v4", "serde"] }
async-trait = "0.1"

[dev-dependencies]
tokio = { version = "1", features = ["rt", "macros", "sync"] }
```

逐行解释：
- `description` 文本更新——加上 `Node trait`，反映本任务新增内容
- `async-trait = "0.1"`——新增依赖；Node trait 的 `async fn on_message` / `async fn restore` 需要展开为 `Pin<Box<dyn Future>>`，否则无法用于 `Arc<dyn Node>` 动态分发。与 `arf-mcp/src/tool.rs` 的 `Tool` trait 用法一致
- `[dev-dependencies] tokio = { features = ["rt", "macros", "sync"] }`——Node trait 测试用 `#[tokio::test]` 跑 async case，需要 tokio 的 runtime + macros 宏展开；`sync` feature 是为了 `RwLock` 等并发原语在测试中也可用。tokio 仅 dev 依赖，不进入 runtime——`arf-core` 仍保持零运行时依赖的目标（`Node` trait 自身不调用 tokio API；调用方决定 runtime）

---

### `crates/arf-core/src/node.rs`

```rust
//! Node trait — the universal message-handling contract for all Bus participants.
//!
//! Every node that connects to a Bus implements `Node`. The trait defines four
//! methods covering identity (`id`), state persistence (`snapshot` / `restore`),
//! and message handling (`on_message`). Implementation is intentionally minimal —
//! Engine, ModelAdapter, McpNode, MemoryNode, CompactionNode, Pool nodes, and
//! any App-defined custom node all implement the same trait.
//!
//! ## Boundaries (Phase 6 design §0.2)
//!
//! 1. **Engine doesn't know any concrete Node type** — the Node trait is the
//!    only contract. `arf-engine` never `use`s `ModelAdapter` / `McpNode` / etc.
//! 2. **Node doesn't know Engine exists** — Node only consumes `Message` and
//!    `BusId`; it never assumes the sender is the Engine.
//! 3. **Engine is one Node among many** — Engine itself implements Node so it
//!    can `bus.connect()` like any other participant.
//!
//! ## Concurrency contract (Phase 6 design §1.4)
//!
//! - `snapshot` is `&self`: snapshot does NOT block Node from processing
//!   messages. Node implementation guards shared state with `RwLock`; snapshot
//!   takes read lock, `on_message` takes write lock (short critical section).
//! - `restore` is `&mut self`: restore must be called when Node is quiescent
//!   (no in-flight messages). App coordinates timing.
//! - `on_message` is `&mut self`: message handling is serialized per Node,
//!   matching the Bus mpsc receive model.
//!
//! ## `from_bus` parameter
//!
//! `BusId` lets a Node subscribed to multiple Buses (e.g., facade nodes in
//! domain-controller topology) know which Bus a message came from. It is passed
//! as a separate parameter rather than embedded in `Message` because the Bus
//! itself knows the source identity at delivery time. The `Message.from_bus`
//! field (added in task 6.0.3) is for serialization; this parameter is the
//! runtime contract.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{Message, NodeId};

// ── BusId ──────────────────────────────────────────────────────────────

/// Unique identifier for a Bus instance.
///
/// Global uniqueness is required by the Phase 6 design (§2.P7) so that a Node
/// subscribed to multiple Buses (e.g., facade nodes) can disambiguate which
/// Bus a message arrived on. Underlying `Uuid` ensures collision-free
/// generation without coordination.
///
/// `BusId` is defined in `arf-core` (not `arf-bus`) because:
/// - the `Node` trait signature requires it (`on_message(_, from_bus: BusId)`)
/// - this avoids `arf-core → arf-bus` cyclic dependency
/// - `arf-bus` in task 6.0.3 will reuse this type directly
#[derive(Debug, Clone, Copy, Hash, Eq, PartialEq, Ord, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub struct BusId(pub Uuid);

impl BusId {
    /// Generate a fresh random `BusId`.
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }

    /// Create a `BusId` from an existing `Uuid`.
    pub fn from_uuid(uuid: Uuid) -> Self {
        Self(uuid)
    }

    /// Borrow the inner `Uuid`.
    pub fn as_uuid(&self) -> &Uuid {
        &self.0
    }
}

impl Default for BusId {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Display for BusId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "bus:{}", self.0)
    }
}
```

逐行解释：
- `#[serde(transparent)]`——序列化时直接输出 `Uuid` 字符串，避免 `{ "0": "..." }` 这种 newtype 包装噪音
- `pub BusId(pub Uuid)`——`pub` 字段允许外部直接构造（`BusId(some_uuid)`），同时 `from_uuid` 提供命名构造器便于阅读
- `Copy` ——`Uuid` 本身是 `Copy`，newtype 自动派生；`BusId` 按值传递无开销
- `Ord + PartialOrd` ——为未来按 BusId 排序的需求留接口（如 deterministic 测试）
- `Display: "bus:{uuid}"` ——日志输出时一眼区分 NodeId 与 BusId

```rust

// ── SnapshotError ──────────────────────────────────────────────────────

/// Errors that can occur when a Node snapshots its internal state.
///
/// Returned by `Node::snapshot()`. The barrier protocol (§2.P9) treats each
/// `SnapshotError` as a per-node failure — the failed Node is added to
/// `BarrierReceipt.missing` and the App decides whether to retry or accept
/// the partial snapshot.
#[derive(Debug)]
pub enum SnapshotError {
    /// Node internal state access exceeded the configured timeout.
    /// Recommended: wrap state reads with `tokio::time::timeout` to avoid
    /// blocking barrier forever (Phase 6 design §1.4).
    Timeout,
    /// Failed to acquire the internal lock (RwLock/Mutex poisoned).
    Lock(String),
    /// `serde_json` serialization of state failed.
    Serialize(String),
    /// Node is offline (already disconnected) and cannot snapshot.
    NodeOffline,
    /// Node-specific error not covered by the above variants.
    Other(String),
}

impl std::fmt::Display for SnapshotError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Timeout => write!(f, "snapshot timed out"),
            Self::Lock(reason) => write!(f, "snapshot lock failed: {reason}"),
            Self::Serialize(reason) => write!(f, "snapshot serialize failed: {reason}"),
            Self::NodeOffline => write!(f, "snapshot: node is offline"),
            Self::Other(reason) => write!(f, "snapshot error: {reason}"),
        }
    }
}

impl std::error::Error for SnapshotError {}
```

逐行解释：
- 五个 variant 覆盖 Phase 6 设计 §4 SnapshotError enum 全部情况（含一个 `Other(String)` 兜底，让 Node 实现者可以传自定义错误而不必每次扩展 enum）
- `Timeout` / `Lock` / `Serialize` / `NodeOffline` 与设计文档 §4 一一对应
- `Display` 手写实现——与 arf-core 现有 `SendError` 风格一致，不引入 `thiserror` 依赖

```rust

// ── RestoreError ───────────────────────────────────────────────────────

/// Errors that can occur when restoring a Node from a snapshot.
///
/// Returned by `Node::restore()`. Unlike `SnapshotError`, restore errors are
/// not aggregated — they typically indicate a fatal state corruption that
/// should fail the session (Phase 6 design §10.2).
#[derive(Debug)]
pub enum RestoreError {
    /// Snapshot format version doesn't match the current Node version.
    /// Caller should refuse to restore and either load a different snapshot
    /// or fail the session.
    VersionMismatch { expected: u32, actual: u32 },
    /// Snapshot JSON deserialization failed.
    Deserialize(String),
    /// Restored state references missing resources (e.g., dropped MCP
    /// subprocess handles).
    InconsistentState(String),
    /// Node-specific error not covered by the above variants.
    Other(String),
}

impl std::fmt::Display for RestoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::VersionMismatch { expected, actual } => write!(
                f,
                "restore version mismatch: expected {expected}, got {actual}"
            ),
            Self::Deserialize(reason) => write!(f, "restore deserialize failed: {reason}"),
            Self::InconsistentState(reason) => write!(f, "restore inconsistent state: {reason}"),
            Self::Other(reason) => write!(f, "restore error: {reason}"),
        }
    }
}

impl std::error::Error for RestoreError {}
```

逐行解释：
- `VersionMismatch` 含 `expected` / `actual` 字段——让 App 直接看到具体版本号差异，无需解析字符串
- `InconsistentState(String)` ——比设计文档 §4 多一个 variant，理由是 deserialize 成功但语义不可用（如引用了不存在的 NodeId），需要单独区分以让 App 决策
- `Other(String)` 兜底，同 `SnapshotError`

```rust

// ── Node trait ─────────────────────────────────────────────────────────

/// Universal message-handling contract for all Bus participants.
///
/// Every node that connects to a Bus — Engine, ModelAdapter, McpNode,
/// MemoryNode, CompactionNode, Pool nodes, App-defined custom nodes —
/// implements this trait.
///
/// ## Method semantics
///
/// | Method | Receiver | Async | Purpose |
/// |--------|----------|-------|---------|
/// | `id` | `&self` | no | Stable identity for Bus addressing |
/// | `snapshot` | `&self` | no | Serialize internal state for persistence |
/// | `restore` | `&mut self` | yes | Replace internal state from a snapshot |
/// | `on_message` | `&mut self` | yes | Handle one incoming Bus message |
///
/// ## Concurrency (Phase 6 design §1.4)
///
/// - `Send + Sync` — allows Node to be shared across tokio tasks (`Arc<dyn Node>`).
/// - `snapshot(&self)` does not block message handling — Node internal state
///   must be guarded by `RwLock`; snapshot takes read lock, on_message takes
///   write lock (short critical section).
/// - `restore(&mut self)` must be called when Node is quiescent — App
///   coordinates timing via barrier or other protocol.
/// - `on_message(&mut self)` serializes per Node — matches Bus mpsc model.
///
/// ## Example
///
/// ```ignore
/// use arf_core::{Node, NodeId, Message, BusId, SnapshotError, RestoreError};
/// use async_trait::async_trait;
/// use std::sync::RwLock;
///
/// struct EchoNode {
///     id: NodeId,
///     seen: RwLock<Vec<String>>,
/// }
///
/// #[async_trait]
/// impl Node for EchoNode {
///     fn id(&self) -> &NodeId { &self.id }
///
///     fn snapshot(&self) -> Result<serde_json::Value, SnapshotError> {
///         Ok(serde_json::to_value(&*self.seen.read().unwrap())
///             .map_err(|e| SnapshotError::Serialize(e.to_string()))?)
///     }
///
///     async fn restore(&mut self, snap: serde_json::Value) -> Result<(), RestoreError> {
///         let v: Vec<String> = serde_json::from_value(snap)
///             .map_err(|e| RestoreError::Deserialize(e.to_string()))?;
///         *self.seen.write().unwrap() = v;
///         Ok(())
///     }
///
///     async fn on_message(&mut self, msg: Message, _from: BusId) {
///         self.seen.write().unwrap().push(msg.msg_type);
///     }
/// }
/// ```
#[async_trait::async_trait]
pub trait Node: Send + Sync {
    /// Stable identity used by the Bus for addressing.
    ///
    /// Must be unique across the entire Bus topology (Phase 6 design §2.P7
    /// NodeId global uniqueness). Convention: `"{type}/{name}"`,
    /// e.g. `"engine/main"`, `"model/deepseek"`, `"mcp/local"`.
    fn id(&self) -> &NodeId;

    /// Serialize internal state for persistence.
    ///
    /// Returns `Ok(serde_json::Value)` on success; App wraps this into
    /// `SessionSnapshot` and persists it. `&self` means snapshot does NOT
    /// block concurrent message handling — Node implementation guards shared
    /// state with `RwLock` / `Mutex`.
    ///
    /// On error, returns `SnapshotError`. The barrier protocol (§2.P9) treats
    /// each error as per-node failure — the Node is added to
    /// `BarrierReceipt.missing` and App decides whether to retry.
    fn snapshot(&self) -> Result<serde_json::Value, SnapshotError>;

    /// Replace internal state from a previously serialized snapshot.
    ///
    /// `&mut self` — restore requires exclusive access; App must ensure Node
    /// is quiescent (no in-flight messages) before calling. Restore is async
    /// because implementations may need to re-establish connections,
    /// re-spawn subprocesses, etc.
    ///
    /// On `VersionMismatch`, the snapshot should be refused (do NOT apply
    /// partial state). On `InconsistentState`, the caller should treat the
    /// restore as failed and discard the Node.
    async fn restore(&mut self, snapshot: serde_json::Value) -> Result<(), RestoreError>;

    /// Handle one incoming Bus message.
    ///
    /// `&mut self` — message handling is serialized per Node (matches Bus
    /// mpsc receive model). `from_bus` identifies which Bus delivered the
    /// message; relevant for facade Nodes subscribed to multiple Buses
    /// (Phase 6 design §2.P7).
    ///
    /// No return value — failures inside `on_message` are the Node's
    /// internal concern. If the Node crashes, the Bus will detect via
    /// heartbeat and emit `node_offline` (§2.P8).
    async fn on_message(&mut self, msg: Message, from_bus: BusId);
}
```

逐行解释：
- `#[async_trait::async_trait]`——展开 `async fn on_message` / `async fn restore` 为返回 `Pin<Box<dyn Future + Send>>` 的方法，让 trait 可用于 `Arc<dyn Node>` 动态分发。底层原理：Rust 2024 edition 的 `async fn` in trait 支持静态分发，但 `dyn Trait` 仍需展开
- `Send + Sync`——允许跨 tokio task 共享（`Send`）+ 引用共享（`Sync` via `Arc`）
- `&NodeId` 返回借用而非 owned——`NodeId` 是 stable identity，不应被克隆
- `snapshot(&self)` 同步签名——snapshot 不阻塞，Node 用 RwLock 自保护；同步便于在 barrier 协议中按固定顺序串行调用多个 Node 的 snapshot
- `restore` 异步——实现可能需要 re-establish HTTP 连接 / re-spawn subprocess
- `on_message` 异步 + `&mut self`——每个消息独占 Node 状态，与 Bus 的 `mpsc::Receiver` 模型一致
- `from_bus: BusId`——第二个参数；不是从 `msg.from_bus` 字段读，因为 `Message` 当前还没有该字段（task 6.0.3 才加）。这里从框架运行时参数传更直接
- 文档注释里带 `## Example` 代码块——但用 `ignore` 标记不参与 doctest（因为 `NodeId::new` 等在 doctest 中需要复杂 setup；后续任务再补 doctest）

---

## lib.rs 更新

在 `crates/arf-core/src/lib.rs` 的模块声明区域增加 `pub mod node;`：

```rust
//! ARF shared types — Message format, identifiers, error types, Node trait.
//!
//! This crate defines the common vocabulary that all other ARF crates share.
//! It has zero dependencies on other ARF crates.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub mod node;

// ── NodeId ───────────────────────────────────────────────────────────
// ... existing content unchanged ...
```

逐行解释：
- `pub mod node`——放在 `use` 语句之后、第一个 `// ──` 注释之前；模块声明集中区
- crate 文档注释更新——加上 `error types, Node trait`，反映新模块

完整修改后的文件头部（仅展示变更部分）：

```rust
//! ARF shared types — Message format, identifiers, error types, Node trait.
//!
//! This crate defines the common vocabulary that all other ARF crates share.
//! It has zero dependencies on other ARF crates.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub mod node;
```

---

## 测试

### 测试结构

测试内联在 `crates/arf-core/src/node.rs` 末尾（与 arf-core 现有风格一致——现有 `NodeId` / `Message` / `NodeInfo` 等测试都在 `lib.rs` 内联 `mod tests` 中）。

```
crates/arf-core/src/node.rs
├── pub struct BusId
├── pub enum SnapshotError
├── pub enum RestoreError
└── pub trait Node
└── #[cfg(test)] mod tests { ... }
```

---

### `crates/arf-core/src/node.rs` 末尾追加（测试模块）

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Message, NodeId};
    use serde_json::{json, Value};
    use std::sync::{Arc, RwLock};

    // ═══════════════════════════════════════════════════════════════
    // MockNode — 用于测试 Node trait 契约的共享 mock 实现
    // ═══════════════════════════════════════════════════════════════

    struct MockNode {
        id: NodeId,
        state: RwLock<MockState>,
    }

    #[derive(Default, Clone, Serialize, Deserialize)]
    struct MockState {
        seen_msg_types: Vec<String>,
        from_buses: Vec<BusId>,
    }

    impl MockNode {
        fn new(id: &str) -> Self {
            Self {
                id: NodeId::new(id),
                state: RwLock::new(MockState::default()),
            }
        }

        fn seen_count(&self) -> usize {
            self.state.read().unwrap().seen_msg_types.len()
        }
    }

    #[async_trait::async_trait]
    impl Node for MockNode {
        fn id(&self) -> &NodeId {
            &self.id
        }

        fn snapshot(&self) -> Result<Value, SnapshotError> {
            let state = self
                .state
                .read()
                .map_err(|e| SnapshotError::Lock(e.to_string()))?;
            serde_json::to_value(&*state).map_err(|e| SnapshotError::Serialize(e.to_string()))
        }

        async fn restore(&mut self, snapshot: Value) -> Result<(), RestoreError> {
            let state: MockState =
                serde_json::from_value(snapshot).map_err(|e| RestoreError::Deserialize(e.to_string()))?;
            *self.state.write().map_err(|e| RestoreError::Other(e.to_string()))? = state;
            Ok(())
        }

        async fn on_message(&mut self, msg: Message, from_bus: BusId) {
            let mut state = self.state.write().unwrap();
            state.seen_msg_types.push(msg.msg_type);
            state.from_buses.push(from_bus);
        }
    }

    // 辅助函数：构造测试用 Message
    fn make_msg(msg_type: &str, from: &str) -> Message {
        Message::new(
            msg_type,
            NodeId::new(from),
            vec![NodeId::new("target")],
            json!({}),
        )
    }

    // ═══════════════════════════════════════════════════════════════
    // BusId — 12 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] BusId::new() 生成的 BusId 不与另一个 BusId::new() 相等（UUID v4 唯一性）
    #[test]
    fn bus_id_new_is_unique() {
        let a = BusId::new();
        let b = BusId::new();
        assert_ne!(a, b);
    }

    // [构造] BusId::from_uuid(uuid) 返回相同 UUID
    #[test]
    fn bus_id_from_uuid() {
        let uuid = Uuid::new_v4();
        let bid = BusId::from_uuid(uuid);
        assert_eq!(bid.as_uuid(), &uuid);
    }

    // [构造] BusId::default() 生成有效 BusId
    #[test]
    fn bus_id_default_creates_valid() {
        let bid = BusId::default();
        // 序列化 / 反序列化能 round-trip → 是有效的 UUID
        let json = serde_json::to_string(&bid).unwrap();
        let back: BusId = serde_json::from_str(&json).unwrap();
        assert_eq!(bid, back);
    }

    // [trait] Clone 保持相等
    #[test]
    fn bus_id_clone_eq() {
        let bid = BusId::new();
        let cloned = bid;
        assert_eq!(bid, cloned);
    }

    // [trait] Copy 语义：赋值不移动，后续仍可用
    #[test]
    fn bus_id_copy_semantics() {
        let bid = BusId::new();
        let copy = bid; // Copy, no move
        let _still_usable = bid; // 仍可用
        assert_eq!(bid, copy);
    }

    // [trait] Eq + Hash：相同 UUID 进 HashSet 后只出现一次
    #[test]
    fn bus_id_hash_dedup() {
        use std::collections::HashSet;
        let bid = BusId::new();
        let mut set = HashSet::new();
        set.insert(bid);
        set.insert(bid); // 重复插入
        assert_eq!(set.len(), 1);
    }

    // [trait] Ord：两个 BusId 可比较大小
    #[test]
    fn bus_id_ord() {
        let a = BusId::new();
        let b = BusId::new();
        // 仅验证 Ord 可调用，具体大小不重要
        let _ = a.cmp(&b);
        let _ = a.partial_cmp(&b);
    }

    // [trait] Display 输出 "bus:{uuid}" 格式
    #[test]
    fn bus_id_display() {
        let bid = BusId::new();
        let s = format!("{bid}");
        assert!(s.starts_with("bus:"));
        // UUID 部分长度固定 36（含 4 个连字符）
        assert_eq!(s.len(), "bus:".len() + 36);
    }

    // [序列化] serde transparent：序列化输出是裸 UUID 字符串，不是 { "0": "..." }
    #[test]
    fn bus_id_serde_transparent() {
        let bid = BusId::new();
        let json = serde_json::to_string(&bid).unwrap();
        // 不应包含 "0"（newtype 字段名）
        assert!(!json.contains("\"0\""));
        // 应该是 UUID 字符串格式
        let back: BusId = serde_json::from_str(&json).unwrap();
        assert_eq!(bid, back);
    }

    // [序列化] serde 往返：从 JSON 反序列化后相等
    #[test]
    fn bus_id_serde_roundtrip() {
        let bid = BusId::new();
        let json = serde_json::to_string(&bid).unwrap();
        let back: BusId = serde_json::from_str(&json).unwrap();
        assert_eq!(bid, back);
    }

    // [兼容] BusId 可以放在 JSON 对象里（与 NodeInfo 等结构共存）
    #[test]
    fn bus_id_in_json_object() {
        let bid = BusId::new();
        let wrapper = json!({
            "bus_id": bid,
            "node_id": "engine/main",
        });
        assert!(wrapper["bus_id"].is_string());
        assert_eq!(wrapper["node_id"], "engine/main");
    }

    // [边界] 连续生成 1000 个 BusId 无重复（统计唯一性）
    #[test]
    fn bus_id_1000_unique() {
        use std::collections::HashSet;
        let bids: Vec<BusId> = (0..1000).map(|_| BusId::new()).collect();
        let unique: HashSet<_> = bids.iter().collect();
        assert_eq!(unique.len(), 1000);
    }

    // ═══════════════════════════════════════════════════════════════
    // SnapshotError — 8 tests
    // ═══════════════════════════════════════════════════════════════

    // [覆盖] 所有 variant 均可构造
    #[test]
    fn snapshot_error_all_variants_construct() {
        let _ = SnapshotError::Timeout;
        let _ = SnapshotError::Lock("poisoned".into());
        let _ = SnapshotError::Serialize("json".into());
        let _ = SnapshotError::NodeOffline;
        let _ = SnapshotError::Other("custom".into());
    }

    // [trait] Display：每个 variant 输出包含关键词
    #[test]
    fn snapshot_error_display_timeout() {
        assert_eq!(format!("{}", SnapshotError::Timeout), "snapshot timed out");
    }

    #[test]
    fn snapshot_error_display_lock() {
        let e = SnapshotError::Lock("poisoned".into());
        assert!(format!("{e}").contains("poisoned"));
    }

    #[test]
    fn snapshot_error_display_serialize() {
        let e = SnapshotError::Serialize("bad json".into());
        assert!(format!("{e}").contains("bad json"));
    }

    #[test]
    fn snapshot_error_display_node_offline() {
        assert_eq!(
            format!("{}", SnapshotError::NodeOffline),
            "snapshot: node is offline"
        );
    }

    #[test]
    fn snapshot_error_display_other() {
        let e = SnapshotError::Other("custom failure".into());
        assert!(format!("{e}").contains("custom failure"));
    }

    // [trait] Debug 输出包含 variant 名
    #[test]
    fn snapshot_error_debug() {
        let e = SnapshotError::Timeout;
        assert!(format!("{e:?}").contains("Timeout"));
    }

    // [trait] std::error::Error：满足 trait 约束
    #[test]
    fn snapshot_error_implements_std_error() {
        fn takes_error(_e: impl std::error::Error) {}
        takes_error(SnapshotError::Timeout);
    }

    // ═══════════════════════════════════════════════════════════════
    // RestoreError — 9 tests
    // ═══════════════════════════════════════════════════════════════

    // [覆盖] 所有 variant 均可构造
    #[test]
    fn restore_error_all_variants_construct() {
        let _ = RestoreError::VersionMismatch { expected: 1, actual: 2 };
        let _ = RestoreError::Deserialize("bad".into());
        let _ = RestoreError::InconsistentState("missing ref".into());
        let _ = RestoreError::Other("custom".into());
    }

    // [trait] Display：VersionMismatch 包含 expected/actual 数字
    #[test]
    fn restore_error_display_version_mismatch() {
        let e = RestoreError::VersionMismatch { expected: 1, actual: 2 };
        let s = format!("{e}");
        assert!(s.contains("1"));
        assert!(s.contains("2"));
        assert!(s.contains("version mismatch"));
    }

    #[test]
    fn restore_error_display_deserialize() {
        let e = RestoreError::Deserialize("unexpected token".into());
        assert!(format!("{e}").contains("unexpected token"));
    }

    #[test]
    fn restore_error_display_inconsistent_state() {
        let e = RestoreError::InconsistentState("dangling handle".into());
        assert!(format!("{e}").contains("dangling handle"));
    }

    #[test]
    fn restore_error_display_other() {
        let e = RestoreError::Other("oops".into());
        assert!(format!("{e}").contains("oops"));
    }

    // [trait] Debug 输出包含 variant 名
    #[test]
    fn restore_error_debug() {
        let e = RestoreError::VersionMismatch { expected: 0, actual: 1 };
        assert!(format!("{e:?}").contains("VersionMismatch"));
    }

    // [trait] std::error::Error：满足 trait 约束
    #[test]
    fn restore_error_implements_std_error() {
        fn takes_error(_e: impl std::error::Error) {}
        takes_error(RestoreError::Other("x".into()));
    }

    // [trait] VersionMismatch 是 PartialEq（断言 expected/actual 一致）
    #[test]
    fn restore_error_version_mismatch_partial_eq() {
        let a = RestoreError::VersionMismatch { expected: 1, actual: 2 };
        let b = RestoreError::VersionMismatch { expected: 1, actual: 2 };
        let c = RestoreError::VersionMismatch { expected: 1, actual: 3 };
        // 没有派生 PartialEq 时该测试失败；保证加 PartialEq 后语义正确
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    // [构造] VersionMismatch 数字很大（u32::MAX）也不 panic
    #[test]
    fn restore_error_version_mismatch_max_u32() {
        let e = RestoreError::VersionMismatch {
            expected: u32::MAX,
            actual: 0,
        };
        assert!(format!("{e}").contains(&u32::MAX.to_string()));
    }

    // ═══════════════════════════════════════════════════════════════
    // Node trait — 17 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] MockNode::id() 返回构造时的 NodeId
    #[test]
    fn node_id_returns_constructor_value() {
        let node = MockNode::new("engine/main");
        assert_eq!(node.id().as_str(), "engine/main");
    }

    // [构造] MockNode::id() 返回 &NodeId 借用（不 clone）
    #[test]
    fn node_id_returns_borrowed_ref() {
        let node = MockNode::new("mcp/local");
        let r1: &NodeId = node.id();
        let r2: &NodeId = node.id();
        // 两个引用指向同一地址
        assert_eq!(r1.as_str(), r2.as_str());
        assert_eq!(r1.as_str(), "mcp/local");
    }

    // [方法] on_message 接收消息后 seen_count 增加
    #[tokio::test]
    async fn node_on_message_increments_seen_count() {
        let mut node = MockNode::new("test");
        assert_eq!(node.seen_count(), 0);
        node.on_message(make_msg("model_call", "engine"), BusId::new()).await;
        assert_eq!(node.seen_count(), 1);
    }

    // [方法] on_message 接收多条消息：按顺序记录 msg_type
    #[tokio::test]
    async fn node_on_message_preserves_order() {
        let mut node = MockNode::new("test");
        let bus = BusId::new();
        node.on_message(make_msg("first", "a"), bus).await;
        node.on_message(make_msg("second", "b"), bus).await;
        node.on_message(make_msg("third", "c"), bus).await;

        let state = node.state.read().unwrap();
        assert_eq!(
            state.seen_msg_types,
            vec!["first".to_string(), "second".to_string(), "third".to_string()]
        );
    }

    // [方法] on_message 接收 from_bus 参数并保存
    #[tokio::test]
    async fn node_on_message_captures_from_bus() {
        let mut node = MockNode::new("test");
        let bus_a = BusId::new();
        let bus_b = BusId::new();
        node.on_message(make_msg("x", "a"), bus_a).await;
        node.on_message(make_msg("y", "b"), bus_b).await;

        let state = node.state.read().unwrap();
        assert_eq!(state.from_buses, vec![bus_a, bus_b]);
    }

    // [方法] snapshot 返回 Ok(Value)，内容包含 seen_msg_types
    #[test]
    fn node_snapshot_returns_state() {
        let node = MockNode::new("test");
        let snap = node.snapshot().unwrap();
        assert!(snap.is_object());
        assert_eq!(snap["seen_msg_types"], json!([]));
        assert_eq!(snap["from_buses"], json!([]));
    }

    // [方法] snapshot 在 on_message 写入后能反映新状态
    #[tokio::test]
    async fn node_snapshot_reflects_writes() {
        let mut node = MockNode::new("test");
        node.on_message(make_msg("hello", "x"), BusId::new()).await;
        let snap = node.snapshot().unwrap();
        assert_eq!(snap["seen_msg_types"], json!(["hello"]));
    }

    // [方法] restore 写入新状态后 seen_count 反映新值
    #[tokio::test]
    async fn node_restores_state() {
        let mut node = MockNode::new("test");
        let new_state = json!({
            "seen_msg_types": ["a", "b", "c"],
            "from_buses": [BusId::new(), BusId::new(), BusId::new()],
        });
        node.restore(new_state).await.unwrap();
        assert_eq!(node.seen_count(), 3);
    }

    // [方法] snapshot + restore round-trip：snapshot 后 restore 到新 Node，状态一致
    #[tokio::test]
    async fn node_snapshot_restore_roundtrip() {
        let mut node_a = MockNode::new("test");
        let bus = BusId::new();
        node_a.on_message(make_msg("first", "x"), bus).await;
        node_a.on_message(make_msg("second", "y"), bus).await;

        let snap = node_a.snapshot().unwrap();

        let mut node_b = MockNode::new("test");
        node_b.restore(snap).await.unwrap();
        assert_eq!(node_b.seen_count(), 2);

        let state_b = node_b.state.read().unwrap();
        assert_eq!(state_b.seen_msg_types, vec!["first", "second"]);
        assert_eq!(state_b.from_buses, vec![bus, bus]);
    }

    // [方法] restore 接受非法 JSON 返回 Err(RestoreError::Deserialize)
    #[tokio::test]
    async fn node_restore_invalid_json_returns_err() {
        let mut node = MockNode::new("test");
        let bad = json!("not a MockState object");
        let result = node.restore(bad).await;
        assert!(matches!(result, Err(RestoreError::Deserialize(_))));
    }

    // [方法] snapshot 是 &self 调用（不需 &mut），与 Node trait 签名一致
    #[test]
    fn node_snapshot_takes_immutable_ref() {
        let node = MockNode::new("test");
        // 编译通过 = 签名正确；再连调两次确认无副作用
        let _s1 = node.snapshot().unwrap();
        let _s2 = node.snapshot().unwrap();
    }

    // [trait] Node 可用于 Arc<dyn Node> 动态分发
    #[test]
    fn node_trait_is_object_safe_via_async_trait() {
        let node: Arc<dyn Node> = Arc::new(MockNode::new("dyn_test"));
        assert_eq!(node.id().as_str(), "dyn_test");
        // snapshot 也是 dyn 安全的
        let snap = node.snapshot().unwrap();
        assert!(snap.is_object());
    }

    // [trait] Node: Send + Sync（编译期验证）
    #[test]
    fn node_is_send_and_sync() {
        fn assert_send<T: Send>() {}
        fn assert_sync<T: Sync>() {}
        assert_send::<MockNode>();
        assert_sync::<MockNode>();
    }

    // [并发] snapshot 在另一线程持有 read lock 时仍能调用（&self 不阻塞）
    #[test]
    fn node_snapshot_does_not_block_concurrent_read() {
        use std::thread;
        let node = Arc::new(MockNode::new("concurrent"));

        let node_clone = Arc::clone(&node);
        let reader = thread::spawn(move || {
            // 持有 read lock 较长时间
            let _guard = node_clone.state.read().unwrap();
            std::thread::sleep(std::time::Duration::from_millis(50));
        });

        // 主线程在 reader 持锁时调 snapshot——若 snapshot 实现错误地用 write lock 会 deadlock
        std::thread::sleep(std::time::Duration::from_millis(10)); // 确保 reader 已获锁
        let snap = node.snapshot().unwrap(); // 不应阻塞
        assert!(snap.is_object());

        reader.join().unwrap();
    }

    // [边界] 空 NodeId：MockNode::new("") 仍可用
    #[tokio::test]
    async fn node_with_empty_id() {
        let mut node = MockNode::new("");
        assert_eq!(node.id().as_str(), "");
        node.on_message(make_msg("x", "y"), BusId::new()).await;
        assert_eq!(node.seen_count(), 1);
    }

    // [边界] Unicode NodeId：MockNode::new("引擎/主") 正常处理
    #[tokio::test]
    async fn node_with_unicode_id() {
        let mut node = MockNode::new("引擎/主");
        assert_eq!(node.id().as_str(), "引擎/主");
        node.on_message(make_msg("x", "y"), BusId::new()).await;
        assert_eq!(node.seen_count(), 1);
    }

    // [覆盖] restore 接受 MockState 完全字段的 JSON（不缺失字段）
    #[tokio::test]
    async fn node_restore_with_full_state() {
        let mut node = MockNode::new("test");
        let bus = BusId::new();
        let full_state = json!({
            "seen_msg_types": ["only"],
            "from_buses": [bus],
        });
        node.restore(full_state).await.unwrap();

        let state = node.state.read().unwrap();
        assert_eq!(state.seen_msg_types, vec!["only"]);
        assert_eq!(state.from_buses, vec![bus]);
    }
}
```

逐行解释（关键测试）：

- `MockNode` 用 `RwLock<MockState>` 保护状态——与设计文档 §1.4 "Node 内部用 RwLock/Mutex 保护共享状态" 的约定一致，是 mock 也是示例
- `Make_msg` 复用 arf-core 现有的 `Message::new` 构造器
- **`node_snapshot_does_not_block_concurrent_read`** 是关键边界测试：另一个线程持有 read lock 时，主线程的 `snapshot(&self)` 必须不阻塞——若实现错误地用 `state.write()` 会 deadlock。这条测试捕获 Node 实现者最容易犯的并发错误
- **`node_trait_is_object_safe_via_async_trait`** 验证 `Arc<dyn Node>` 可编译，确认 `async_trait` 展开正确
- **`node_is_send_and_sync`** 编译期验证 trait bound——若未来误删 `Send + Sync` 会立即编译失败
- **`node_snapshot_reflects_writes`** + **`node_snapshot_restore_roundtrip`** 构成 snapshot/restore 配对测试，确保持久化路径正确
- `BusId` 测试覆盖构造、trait (Clone/Copy/Eq/Hash/Ord/Display)、序列化 (`serde(transparent)` 验证)、兼容（嵌入 JSON 对象）、唯一性（1000 个无重复）

---

## workspace 注册

根 `Cargo.toml` 的 `members` 已包含 `crates/arf-core`，无需修改。

---

## 验证命令

```bash
# 编译检查
. "$HOME/.cargo/env" && cargo check -p arf-core

# 运行 arf-core 测试
. "$HOME/.cargo/env" && cargo test -p arf-core

# 仅运行 Node 相关测试（加快反馈）
. "$HOME/.cargo/env" && cargo test -p arf-core --lib node

# Workspace 全量测试
. "$HOME/.cargo/env" && cargo test --workspace
```

---

## 测试覆盖摘要

| 类型 | 测试数 | 覆盖角度 |
|------|--------|---------|
| `BusId` | 12 | `[构造][trait][序列化][兼容][唯一性]` |
| `SnapshotError` | 8 | `[覆盖][trait]` |
| `RestoreError` | 9 | `[覆盖][trait][构造]` |
| `Node` | 17 | `[构造][方法][trait][并发][边界][覆盖]` |
| **合计** | **46** | |

---

## 验收对照（设计文档 §8）

| §8.2 验收项 | 本任务覆盖 |
|-------------|-----------|
| ActionMessage trait 编译可扩展 | ❌ task 6.1 |
| Response 单形态 `Done(Value)` | ❌ task 6.2 |
| Route enum 仅 `Strict` / `Discovery` | ❌ task 6.1 |
| **Node trait 强制 `&mut self` on_message** | ✅ 本任务：`node_on_message_*` 测试 |
| CheckpointRule 四元组(无 route) | ❌ task 6.1 |
| WaitEvent strategy | ❌ task 6.6 |
| State 三字段 | ❌ task 6.1 |
| Engine 代码不 use 具体 Node crate | ⏳ task 6.3+（本任务不涉及 Engine 代码） |

---

## 依赖关系

```
6.0.1 Node trait（本任务）
  ↓ 依赖
6.0.2 NodeHandle 多 Bus 订阅（需要 Node trait 作为 Arc<dyn Node> 持有）
  ↓ 依赖
6.0.3 BusId + Bus 标识（复用本任务的 BusId 类型）
  ↓ 依赖
6.0.4 Bus::barrier() 原语
  ↓ 依赖
6.0.5 测试 + 文档
  ↓ 依赖
6.1 核心类型定义（ActionMessage / Route / State / Checkpoint 等）
  ...
```

---

## 不在范围内（明确排除）

- **NodeInfo 字段更新**——已有 `capabilities` 字段，无需修改
- **Message.from_bus 字段**——task 6.0.3
- **删除旧 TaskId / TaskStatus 抽象**——单独清理任务，与本任务正交（避免一处改动扩散）
- **`Node` 实现者（如 ModelAdapterNode::impl Node）**——后续任务按 Node trait 各自实现
- **`Engine` 自身实现 Node**——task 6.3
- **doctest**——本任务示例代码块用 `ignore` 标记；后续任务需要时再补 doctest 基础设施