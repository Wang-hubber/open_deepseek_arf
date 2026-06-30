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

// ── RestoreError ───────────────────────────────────────────────────────

/// Errors that can occur when restoring a Node from a snapshot.
///
/// Returned by `Node::restore()`. Unlike `SnapshotError`, restore errors are
/// not aggregated — they typically indicate a fatal state corruption that
/// should fail the session (Phase 6 design §10.2).
#[derive(Debug, PartialEq, Eq)]
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
            let state: MockState = serde_json::from_value(snapshot)
                .map_err(|e| RestoreError::Deserialize(e.to_string()))?;
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
    // Node trait — 16 tests
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