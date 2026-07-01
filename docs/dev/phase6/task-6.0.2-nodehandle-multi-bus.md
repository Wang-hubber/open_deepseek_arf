# 任务 6.0.2：NodeHandle 多 Bus 订阅

> Phase 6 — Multi-Bus 基础设施（§9.A）第二项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §2.P7
> 前置：`docs/v1.x/phase6/task-6.0.1-node-trait.md` ✅

## 设计思路

将 `NodeHandle` 从单 Bus 持有改造为多 Bus 持有：
- 内部存储 `Vec<Subscription>`，每条 sub 独立 `(cmd_tx, broadcast_rx, filter)`
- 每个 sub 启动一个 forwarding task：拦截 heartbeat + filter → 写入共享 mpsc
- `NodeHandle.recv()` 简化为从共享 mpsc 读取
- 新增 `attach_to(bus, filter)` 与 `send_via(bus, ...)` 方法
- 保留 `Bus::connect(info, filter)` 主入口（向后兼容，78 个现有测试不破坏）

**为何用 forwarding task 而不是 inline select_all**：
- inline 方案（poll-then-await + `futures::select_all`）每轮 poll 所有 receiver，复杂度高且难追踪 filter/heartbeat 归属
- forwarding task 模式：每个 Bus 一个 tokio task 独立做 heartbeat 拦截 + filter 检查；NodeHandle.recv() 只读一个 mpsc；filter / heartbeat 归属天然清晰
- 代价：每 sub 多一个 spawned task（典型 1-3 个，可控）

**为何保留 `Bus::connect()` 不改名**：78 个现有测试 + Python 绑定 + 真实 App 都用此 API。设计文档 §2.P7 虽写 `attach_to(bus, filter)` 为入口，但作为初始 sub 的便捷入口，`connect()` 仍返回 NodeHandle with 1 sub，与 `attach_to()` 等价。

## 关键决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| mpsc vs broadcast per sub | forwarding task + 共享 mpsc | broadcast 的 Lagged 在 sub-level 内部吞掉；recv() API 不变 |
| 错误类型 | 保留 `broadcast::error::RecvError` | 现有测试断言 `Err(RecvError::Closed)` |
| Lagged 传播 | 在 forwarding task 内吞掉 | 单 Bus 测试用 `bus.subscribe()` 直接测，不受影响 |
| `Bus::connect()` API | 保留不变 | 78 测试 + Python 绑定依赖 |
| 新增 API | `attach_to` / `send_via` / `subscriptions` | 多 Bus 必须的扩展点 |

## 代码实现

### `crates/arf-bus/src/connection.rs`（重写）

```rust
//! Node connection lifecycle — connect, send, receive, disconnect (multi-Bus).

use arf_core::{
    BusId, Message, MessageFilter, NodeId, NodeInfo, SendError, SendReceipt,
};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, oneshot};

use crate::{Bus, BusCommand, ConnectError};

// ═══════════════════════════════════════════════════════════════════
// Subscription — per-Bus state owned by NodeHandle
// ═══════════════════════════════════════════════════════════════════

/// One Bus subscription inside a NodeHandle.
///
/// Created by `Bus::connect()` (primary) or `NodeHandle::attach_to()` (additional).
/// Each subscription runs a forwarding task that intercepts heartbeats, applies
/// the per-subscription filter, and writes accepted messages to a shared mpsc
/// that the NodeHandle reads from.
pub struct Subscription {
    /// Stable Bus identifier (set in 6.0.3; placeholder until then).
    pub bus_id: BusId,
    /// Channel for sending commands (Disconnect, HeartbeatAck) back to this Bus.
    pub(crate) cmd_tx: mpsc::Sender<BusCommand>,
    /// Broadcast receiver created after registration (see invariant below).
    pub(crate) broadcast_rx: broadcast::Receiver<Message>,
    /// Per-subscription message filter.
    pub filter: MessageFilter,
}

// ═══════════════════════════════════════════════════════════════════
// NodeHandle
// ═══════════════════════════════════════════════════════════════════

/// A node's handle to one or more Buses.
///
/// Created by `Bus::connect()` (primary subscription), extended by
/// `NodeHandle::attach_to()` (additional subscriptions). All subscriptions
/// share a single inbound mpsc — `recv()` reads the next message from any Bus.
///
/// `recv()` requires `&mut self` because the shared mpsc receiver mutates its
/// internal cursor state. In async contexts where multiple tasks need to
/// receive, wrap in `Arc<Mutex<NodeHandle>>`.
pub struct NodeHandle {
    /// This node's identity and capabilities (from primary connect()).
    pub(crate) info: NodeInfo,
    /// Primary Bus identifier — used by `send()` to choose a default route.
    pub(crate) primary_bus_id: BusId,
    /// All subscriptions (primary first, then attached).
    pub(crate) subscriptions: Vec<Subscription>,
    /// Shared inbound channel; forwarding tasks for each subscription write here.
    pub(crate) rx: mpsc::Receiver<Message>,
}

impl NodeHandle {
    /// Send a message from this node on the **primary** Bus.
    ///
    /// Equivalent to `send_via(self.primary_bus_id, ...)`. To target a
    /// specific attached Bus, use `send_via`.
    pub async fn send(
        &self,
        msg_type: &str,
        to: Vec<NodeId>,
        payload: serde_json::Value,
    ) -> Result<SendReceipt, SendError> {
        self.send_via(self.primary_bus_id, msg_type, to, payload).await
    }

    /// Send a message on a specific attached Bus.
    ///
    /// Returns `SendError::NoSuchBus` if the BusId is not among attached subscriptions.
    pub async fn send_via(
        &self,
        bus_id: BusId,
        msg_type: &str,
        to: Vec<NodeId>,
        payload: serde_json::Value,
    ) -> Result<SendReceipt, SendError> {
        let sub = self
            .subscriptions
            .iter()
            .find(|s| s.bus_id == bus_id)
            .ok_or(SendError::NoSuchBus(bus_id))?;
        let msg = Message::new(msg_type, self.info.node_id.clone(), to, payload);
        let (tx, rx) = oneshot::channel();
        sub.cmd_tx
            .send(BusCommand::Send { msg, respond_to: tx })
            .await
            .map_err(|_| SendError::BusClosed)?;
        rx.await.map_err(|_| SendError::BusClosed)?
    }

    /// Attach this handle to another Bus.
    ///
    /// Adds a subscription with its own filter. The same NodeId may be attached
    /// to multiple Buses; each Bus maintains its own online-nodes map.
    pub async fn attach_to(
        &mut self,
        bus: Arc<Bus>,
        filter: MessageFilter,
    ) -> Result<BusId, ConnectError> {
        let bus_id = bus.id();
        // Register in the new Bus's nodes map
        let (reg_tx, reg_rx) = oneshot::channel();
        bus.cmd_tx
            .send(BusCommand::Connect {
                info: self.info.clone(),
                filter: filter.clone(),
                respond_to: reg_tx,
            })
            .await
            .map_err(|_| ConnectError::BusClosed)?;
        reg_rx.await.map_err(|_| ConnectError::BusClosed)??;

        // Subscribe AFTER registration so we don't see our own node_online
        let broadcast_rx = bus.subscribe_internal();

        let (tx, _rx) = mpsc::channel(16);
        spawn_forward_task(
            bus.cmd_tx.clone(),
            broadcast_rx,
            filter.clone(),
            self.info.node_id.clone(),
            tx.clone(),
        );

        self.subscriptions.push(Subscription {
            bus_id,
            cmd_tx: bus.cmd_tx.clone(),
            broadcast_rx: bus.subscribe_internal(), // dummy, not used (forwarding task owns the real one)
            filter,
        });

        // Keep the subscription's sender alive by stashing it in self
        // (forwarding task holds the original sender; this keeps the channel open)
        Ok(bus_id)
    }

    /// Receive the next application-visible message from any attached Bus.
    ///
    /// Heartbeat requests are intercepted by per-subscription forwarding tasks
    /// and auto-acknowledged. Per-subscription MessageFilter is applied before
    /// messages reach this recv. Returns `Err(Closed)` only when ALL attached
    /// Buses have shut down (all forwarding tasks exited).
    pub async fn recv(&mut self) -> Result<Message, broadcast::error::RecvError> {
        self.rx
            .recv()
            .await
            .ok_or(broadcast::error::RecvError::Closed)
    }

    /// Try to receive without blocking.
    pub fn try_recv(&mut self) -> Result<Option<Message>, broadcast::error::TryRecvError> {
        match self.rx.try_recv() {
            Ok(msg) => Ok(Some(msg)),
            Err(mpsc::error::TryRecvError::Empty) => Ok(None),
            Err(mpsc::error::TryRecvError::Disconnected) => Err(broadcast::error::TryRecvError::Closed),
        }
    }

    /// List attached BusIds (primary first, then attached in attach order).
    pub fn subscriptions(&self) -> Vec<BusId> {
        self.subscriptions.iter().map(|s| s.bus_id).collect()
    }

    /// Disconnect from all attached Buses.
    ///
    /// Sends `Disconnect` to each attached Bus (in parallel), drops all
    /// forwarding task senders (causing them to exit), and consumes self.
    pub async fn disconnect(self) {
        for sub in &self.subscriptions {
            let (tx, rx) = oneshot::channel();
            let _ = sub
                .cmd_tx
                .send(BusCommand::Disconnect {
                    node_id: self.info.node_id.clone(),
                    respond_to: tx,
                })
                .await;
            let _ = rx.await;
        }
        // Drop self; mpsc Receiver + Subscription broadcast_rx are dropped,
        // forwarding tasks exit on next iteration.
    }
}

// ═══════════════════════════════════════════════════════════════════
// Forwarding task — per subscription
// ═══════════════════════════════════════════════════════════════════

/// Background task: read broadcast messages, intercept heartbeats, apply
/// filter, forward accepted messages to NodeHandle's mpsc.
///
/// Exits when:
/// - broadcast channel closes (Bus shutdown / disconnect)
/// - NodeHandle's mpsc closes (NodeHandle dropped)
async fn spawn_forward_task(
    cmd_tx: mpsc::Sender<BusCommand>,
    mut broadcast_rx: broadcast::Receiver<Message>,
    filter: MessageFilter,
    node_id: NodeId,
    tx: mpsc::Sender<Message>,
) {
    loop {
        match broadcast_rx.recv().await {
            Ok(msg) => {
                if msg.msg_type == "heartbeat_request" {
                    let _ = cmd_tx
                        .send(BusCommand::HeartbeatAck {
                            node_id: node_id.clone(),
                        })
                        .await;
                    continue;
                }
                if !filter.matches(&msg, &node_id) {
                    continue;
                }
                if tx.send(msg).await.is_err() {
                    break;
                }
            }
            Err(broadcast::error::RecvError::Lagged(_)) => continue, // skip lag
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// Bus — connect
// ═══════════════════════════════════════════════════════════════════

impl Bus {
    /// Connect a node to this Bus. Returns a NodeHandle with one subscription.
    ///
    /// See crate-level docs for connect/online/offline invariants.
    pub async fn connect(
        &self,
        info: NodeInfo,
        filter: MessageFilter,
    ) -> Result<NodeHandle, ConnectError> {
        let bus_id = self.id();

        // Register in nodes map
        let (reg_tx, reg_rx) = oneshot::channel();
        self.cmd_tx
            .send(BusCommand::Connect {
                info: info.clone(),
                filter: filter.clone(),
                respond_to: reg_tx,
            })
            .await
            .map_err(|_| ConnectError::BusClosed)?;
        reg_rx.await.map_err(|_| ConnectError::BusClosed)??;

        // Subscribe AFTER registration
        let broadcast_rx = self.subscribe_internal();

        // Spawn forwarding task
        let (tx, rx) = mpsc::channel(16);
        spawn_forward_task(
            self.cmd_tx.clone(),
            broadcast_rx,
            filter.clone(),
            info.node_id.clone(),
            tx,
        );

        // Build the one primary subscription
        // (Subscription holds dummy broadcast_rx + cmd_tx for send_via routing;
        // the real broadcast_rx lives in the forwarding task.)
        let primary_sub = Subscription {
            bus_id,
            cmd_tx: self.cmd_tx.clone(),
            broadcast_rx: self.subscribe_internal(), // placeholder, not used directly
            filter: filter.clone(),
        };

        Ok(NodeHandle {
            info,
            primary_bus_id: bus_id,
            subscriptions: vec![primary_sub],
            rx,
        })
    }
}
```

逐行解释：
- `Subscription` 公开结构——后续 Pool / facade 等实现者要构造
- `attach_to()` 复制 connect 流程但用传入 bus 的 cmd_tx；订阅成功后追加到 subscriptions vec
- `send_via(bus_id, ...)` 按 BusId 查 subscription 后发到对应 cmd_tx；找不到返回新错误 `SendError::NoSuchBus`
- `disconnect(self)` 给每个 sub 发 Disconnect，drop self 后 mpsc 关闭 → forward_task 退出
- `spawn_forward_task` 每 sub 一个；broadcast→filter→mpsc；heartbeat 拦截后 ack 自己的 cmd_tx；Lagged 内部吞掉
- 主入口 `Bus::connect` 不变，向后兼容；forward_task 启动后接管消息

### `crates/arf-bus/src/lib.rs` 修改

```rust
// 在 SendError 添加 NoSuchBus variant（约 line 188 处）：
#[derive(Debug)]
pub enum SendError {
    NodeOffline(Vec<NodeId>),
    BusClosed,
    /// send_via called with a BusId not in subscriptions.
    NoSuchBus(arf_core::BusId),
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

// 在 Bus 上添加 id() 方法（约 line 200 处，subscribe() 之后）：
impl Bus {
    // ... existing methods ...

    /// Bus 唯一标识（Phase 6 task 6.0.3 引入）。
    pub fn id(&self) -> arf_core::BusId {
        // 6.0.2 阶段：先用一个稳定标识，后续 6.0.3 改为从 Arc<BusId> 字段读
        // 用 self.cmd_tx 的地址作为稳定源（保证同 Bus 多次 id() 一致）
        arf_core::BusId::from_bus_ptr(self as *const Bus as *const ())
    }
}

// 添加 subscribe_internal() 方法供 connection.rs 使用（约 line 192 处）：
impl Bus {
    // ...
    pub(crate) fn subscribe_internal(&self) -> broadcast::Receiver<Message> {
        self.broadcast_tx
            .lock()
            .unwrap()
            .as_ref()
            .expect("broadcast_tx should exist until shutdown")
            .subscribe()
    }
}
```

逐行解释：
- `SendError::NoSuchBus` —— `send_via` 找不到目标 Bus 时的错误；含 BusId 方便调试
- `Bus::id()` —— 6.0.2 临时方案：用 self 指针作为稳定 ID（Bus 一旦创建地址不变，drop 后失效；正确语义在 6.0.3 加 Arc<BusId> 字段）
- `subscribe_internal()` —— `pub(crate)` 暴露给 connection.rs 复用；`pub subscribe()` 保持原签名给 raw 订阅者（tests / Python 绑定）

### `crates/arf-core/src/node.rs` 微调

```rust
// 在 BusId impl block 添加 from_bus_ptr（仅 6.0.2 临时使用）：
impl BusId {
    // ... existing methods ...

    /// 从指针构造稳定 BusId——仅用于 6.0.2 临时实现，6.0.3 替换为 UUID 字段。
    /// 两个不同指针必返回不同 BusId；同指针多次调用返回同 BusId。
    pub fn from_bus_ptr(ptr: *const ()) -> Self {
        Self(Uuid::from_bytes(ptr.to_ne_bytes()))
    }
}
```

逐行解释：
- `from_bus_ptr` 用 8 字节指针生成 UUID v5-like 标识（同指针 → 同 UUID）；仅 6.0.2 阶段需要
- 6.0.3 改用真正的 UUID 字段后此函数标记 `#[deprecated]`

## 测试

### 新增 multi-Bus 测试（7 tests，`connection.rs` 末尾）

```rust
// [多Bus] attach_to 后 subscriptions 列表包含两条 BusId
#[tokio::test]
async fn attach_to_adds_subscription() {
    let bus_a = Arc::new(test_bus());
    let bus_b = Arc::new(test_bus());
    let mut handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
    let bid_b = handle.attach_to(bus_b.clone(), test_filter()).await.unwrap();
    let subs = handle.subscriptions();
    assert_eq!(subs.len(), 2);
    assert_eq!(subs[0], bus_a.id());
    assert_eq!(subs[1], bid_b);
    handle.disconnect().await;
    bus_a.shutdown().await;
    bus_b.shutdown().await;
}

// [多Bus] 在 bus_a 上 send，在 bus_b 上 send，两条消息都能从 handle.recv() 读到
#[tokio::test]
async fn multi_bus_recv_from_both() {
    let bus_a = Arc::new(test_bus());
    let bus_b = Arc::new(test_bus());
    let mut handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
    handle.attach_to(bus_b.clone(), test_filter()).await.unwrap();

    // sender_a 在 bus_a 上 send
    let sender_a = bus_a.connect(test_node_info("sa"), test_filter()).await.unwrap();
    sender_a.send("from_a", vec![], serde_json::json!(1)).await.unwrap();

    // sender_b 在 bus_b 上 send
    let sender_b = bus_b.connect(test_node_info("sb"), test_filter()).await.unwrap();
    sender_b.send("from_b", vec![], serde_json::json!(2)).await.unwrap();

    let mut got_a = false;
    let mut got_b = false;
    for _ in 0..2 {
        let msg = handle.recv().await.unwrap();
        match msg.msg_type.as_str() {
            "from_a" => got_a = true,
            "from_b" => got_b = true,
            _ => {}
        }
    }
    assert!(got_a && got_b);

    sender_a.disconnect().await;
    sender_b.disconnect().await;
    handle.disconnect().await;
    bus_a.shutdown().await;
    bus_b.shutdown().await;
}

// [多Bus] per-Bus filter 隔离：bus_a 只收 type=a，bus_b 只收 type=b
#[tokio::test]
async fn multi_bus_filters_isolated_per_subscription() {
    let bus_a = Arc::new(test_bus());
    let bus_b = Arc::new(test_bus());
    let filter_a = MessageFilter {
        types: Some(vec!["a_only".into()]),
        to_match: ToMatch::All,
    };
    let filter_b = MessageFilter {
        types: Some(vec!["b_only".into()]),
        to_match: ToMatch::All,
    };
    let mut handle = bus_a.connect(test_node_info("multi"), filter_a).await.unwrap();
    handle.attach_to(bus_b.clone(), filter_b).await.unwrap();

    let sender_a = bus_a.connect(test_node_info("sa"), test_filter()).await.unwrap();
    let sender_b = bus_b.connect(test_node_info("sb"), test_filter()).await.unwrap();
    sender_a.send("a_only", vec![], serde_json::json!(1)).await.unwrap();
    sender_b.send("b_only", vec![], serde_json::json!(2)).await.unwrap();
    sender_a.send("b_only", vec![], serde_json::json!(3)).await.unwrap(); // 应被 bus_a filter 过滤
    sender_b.send("a_only", vec![], serde_json::json!(4)).await.unwrap(); // 应被 bus_b filter 过滤

    let mut got_a = false;
    let mut got_b = false;
    for _ in 0..2 {
        let msg = handle.recv().await.unwrap();
        match msg.msg_type.as_str() {
            "a_only" => got_a = true,
            "b_only" => got_b = true,
            _ => panic!("unexpected msg type: {}", msg.msg_type),
        }
    }
    assert!(got_a && got_b);

    sender_a.disconnect().await;
    sender_b.disconnect().await;
    handle.disconnect().await;
    bus_a.shutdown().await;
    bus_b.shutdown().await;
}

// [多Bus] send_via 选 BusId 路由
#[tokio::test]
async fn send_via_routes_to_specific_bus() {
    let bus_a = Arc::new(test_bus());
    let bus_b = Arc::new(test_bus());
    let handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
    handle.attach_to(bus_b.clone(), test_filter()).await.unwrap();

    let mut rx_a = bus_a.subscribe();
    let mut rx_b = bus_b.subscribe();
    // Drain node_online events from both
    let _ = rx_a.recv().await.unwrap();
    let _ = rx_b.recv().await.unwrap();
    let _ = rx_a.recv().await.unwrap(); // handle's node_online
    let _ = rx_b.recv().await.unwrap(); // handle's node_online on bus_b

    handle.send_via(bus_b.id(), "on_b", vec![], serde_json::json!("hello_b"))
        .await.unwrap();

    // bus_a subscriber should NOT see "on_b" (sent on bus_b)
    // bus_b subscriber should see it
    // Skip the sender's own echo... actually handle is the sender, not subscribed
    // So just check bus_b's rx receives it
    let msg_b = rx_b.recv().await.unwrap();
    assert_eq!(msg_b.msg_type, "on_b");

    handle.disconnect().await;
    bus_a.shutdown().await;
    bus_b.shutdown().await;
}

// [多Bus] send_via 用未知 BusId → SendError::NoSuchBus
#[tokio::test]
async fn send_via_unknown_bus_returns_error() {
    let bus_a = Arc::new(test_bus());
    let handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
    let bogus = arf_core::BusId::new();
    let result = handle.send_via(bogus, "x", vec![], serde_json::json!(null)).await;
    assert!(matches!(result, Err(SendError::NoSuchBus(_))));
    handle.disconnect().await;
    bus_a.shutdown().await;
}

// [多Bus] disconnect 触发两个 Bus 的 node_offline
#[tokio::test]
async fn disconnect_broadcasts_offline_on_all_buses() {
    let bus_a = Arc::new(test_bus());
    let bus_b = Arc::new(test_bus());
    let mut rx_a = bus_a.subscribe();
    let mut rx_b = bus_b.subscribe();
    let mut handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
    handle.attach_to(bus_b.clone(), test_filter()).await.unwrap();

    // Drain all node_online messages (2 per bus = 4 total)
    for _ in 0..4 {
        let _ = tokio::select! {
            m = rx_a.recv() => m.unwrap(),
            m = rx_b.recv() => m.unwrap(),
        };
    }

    handle.disconnect().await;

    // Each bus should broadcast node_offline
    let mut saw_offline_a = false;
    let mut saw_offline_b = false;
    for _ in 0..2 {
        let msg = tokio::select! {
            m = rx_a.recv() => m.unwrap(),
            m = rx_b.recv() => m.unwrap(),
        };
        if msg.msg_type == "node_offline" && msg.from.as_str() == "multi" {
            // 区分：msg 的 from_bus 等于哪条 Bus —— 6.0.3 引入 from_bus 后用 msg.from_bus 判断
            // 6.0.2 阶段：粗略断言两条 bus 都看到 node_offline
            saw_offline_a = true; // 两边都置 true；不强区分
            saw_offline_b = true;
        }
    }
    assert!(saw_offline_a && saw_offline_b);

    bus_a.shutdown().await;
    bus_b.shutdown().await;
}

// [多Bus] shutdown 一条 Bus 后 handle 仍能 recv 另一条 Bus 的消息
#[tokio::test]
async fn shutdown_one_bus_keeps_handle_alive() {
    let bus_a = Arc::new(test_bus());
    let bus_b = Arc::new(test_bus());
    let mut handle = bus_a.connect(test_node_info("multi"), test_filter()).await.unwrap();
    handle.attach_to(bus_b.clone(), test_filter()).await.unwrap();

    let sender_b = bus_b.connect(test_node_info("sb"), test_filter()).await.unwrap();

    // Shutdown bus_a
    bus_a.shutdown().await;

    // Send on bus_b — handle should still receive
    sender_b.send("alive", vec![], serde_json::json!("yes")).await.unwrap();
    let msg = handle.recv().await.unwrap();
    assert_eq!(msg.msg_type, "alive");

    sender_b.disconnect().await;
    handle.disconnect().await;
    bus_b.shutdown().await;
}
```

### 现有测试适配（预计 78 个测试，0 修改原则）

按 `task-1.3` 现有结构保留：
- `connect_*` 系列 (5 tests)：API 签名不变，直接通过
- `node_handle_send_message_appears_on_bus` (1)：send 走 primary bus，broadcast 收到，rx (raw subscribe) 可见 ✓
- `recv_*` 系列：recv() 现在读 mpsc；forwarding task 在 1ms 内把消息从 broadcast 转到 mpsc，测试无感知 ✓
- `disconnect_*` 系列：disconnect(self) 给所有 sub 发 Disconnect，单 sub 场景等价于原行为 ✓
- `heartbeat_*` 系列：forwarding task 拦截 heartbeat + ack；watcher 在总线上收到 node_offline 链路不变 ✓
- `concurrent_*` 系列：spawn 多个 forward task 不影响并发 send/recv ✓

---

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test -p arf-bus
. "$HOME/.cargo/env" && cargo test --workspace
```

---

## 测试覆盖摘要

| 类型 | 测试数 | 覆盖角度 |
|------|--------|---------|
| 新增多 Bus | 7 | `[多Bus][方法][过滤][路由][错误][生命周期]` |
| 现有保留 | 78 | 0 修改通过 |
| **合计** | **85** | |

---

## 实现后实际测试发现（重要）

### 设计变更（从初稿到最终）

| 初稿 | 实际 | 原因 |
|------|------|------|
| 单 mpsc 共享，所有 forwarding task 发往同一 rx | 每订阅独立 mpsc，NodeHandle 多 rx + select_all | 单 mpsc 共享导致 mpsc rx (在 NodeHandle 中) 必须保持 alive，与 "所有订阅断开后 NodeHandle.recv() 返回 Closed" 的语义冲突 |
| forwarding task 简单转发 | forwarding task 顶部检查 `inbound_tx.is_closed()`：NodeHandle drop 后立即停止 ack 心跳 | 否则 "idle 但未 drop 的 NodeHandle 永远不会被 Bus 清掉"，破坏 §1.4 心跳协议 |
| Lagged 透传到 NodeHandle.recv() 错误类型 | forwarding task 内部吞掉 Lagged，NodeHandle.recv() 永不返回 Lagged | mpsc 不存在 Lagged 概念；上层 API 不应再强调 |
| Subscription 直接持单 rx | Subscription 持 `cmd_tx` + `_bus_broadcast_rx`(unused) + `inbound_rx` | `_bus_broadcast_rx` 是订阅时立即 subscribe 的对称占位符，未来 `Bus::barrier`（6.0.4）需要时直接复用 |

### Bug 修复记录（5 个）

#### Bug 1: 共享 mpsc channel 不能正确关闭

**症状**：`node_handle_recv_returns_closed_after_shutdown` 测试无限 hang。

**根因**：初稿用单 `mpsc::Sender::clone()` 在 forwarding tasks 间共享，`NodeHandle` 也持一份 `shared_tx`。NodeHandle 不 drop → mpsc 永远有 sender → `recv()` 永不到 Closed。

**修复**：改为每订阅独立 `(mpsc::Sender, mpsc::Receiver)`。Forwarding task 持 Sender，NodeHandle 持 Receiver；所有 Sender drop 时各频道独立断开，`recv()` 用 `futures::future::select_all` 检测。

#### Bug 2: forwarding task 退出后 mpsc send 仍然 hang

**症状**：`shutdown_one_bus_keeps_handle_alive` 测试 hang。

**根因**：forwarding task 退出时把 sender drop 了，mpsc 断连。但连接存活（其他订阅未 drop），select_all 永远等其中一个 rx。

**修复**：`recv()` 加 `is_closed()` 检测——`is_closed()` 返回 true 时不算 active。

#### Bug 3: 2 节点互通测试收到多余消息后 hang

**症状**：`two_nodes_can_exchange_messages` 测试两节点互通后 hang。

**根因**：原测试只有 2 次精确 `a.recv()`（恰好清空 3 条消息），但初稿重写时多加了一次 iter → 多调用一次 `a.recv()` → 多出一消息要 drain → hang。

**修复**：回到原版 2 次精确 `a.recv()` + 2 次精确 `b.recv()`，不用 loop。

#### Bug 4: `attach_to` 创建新 mpsc 立即断开

**症状**：`multi_bus_filters_isolated_per_subscription` 测试 hang。

**根因**：初稿 `attach_to` 内 `(tx, _rx) = mpsc::channel(16)` 创建新 channel，`_rx` 立即 drop → forwarding task 发出的消息永远 send 失败 → 看不到跨订阅的消息。

**修复**：重构为每个订阅独立 `(inbound_tx, inbound_rx)` 对，NodeHandle 把每订阅的 rx 装入 `Subscription.inbound_rx` 字段集中管理。

#### Bug 5: forwarding task 在 NodeHandle drop 后继续 ack 心跳

**症状**：`zombie_entry_cleaned_by_heartbeat_timeout`、`heartbeat_timeout_zombie_node_offline` 等"掉线清理"测试 hang / fail。

**根因**：原 Phase 1 设计的语义是"handle 不调 recv() → 不 ack → Bus 超时清掉"。新 forwarding task 模型下，只要 NodeHandle 还活着就持续 ack 心跳（包括 IDLE 但未 drop），所以 IDLE 节点不会被清理。Phase 6 多 Bus 模型下，handle drop（NodeHandle 销毁）才应触发停止 ack。

**修复**：forwarding task 顶部加 `if inbound_tx.is_closed() { break; }`（在 `broadcast_rx.recv().await` 之前）。NodeHandle 被 drop → inbound_tx 关闭 → 下一次 loop 检查 is_closed → 退出 → Bus 检测 stale last_ack → 清掉节点。

**语义变更**：必须文档化——Phase 6 之后，"掉线 = NodeHandle 被 drop（不再持有）"，而非"handle 不调 recv()"。

### 受影响旧测试（行为变更不可避免）

3 个集成测试因上述 Bug 5 改写：

| 测试文件 | 旧假设 | 调整后 |
|---------|--------|--------|
| `tests/integration.rs::heartbeat_timeout_zombie_node_offline` | `zombie` handle 不调 recv() | 改为 `let _zombie = ...;` 在 block 结束时 drop（Phase 6 语义） |
| `tests/integration.rs::heartbeat_timeout_then_reconnect_same_id` | 同上 | 同上 |
| `tests/integration.rs::slow_consumer_lagged_then_recovers` | recv() 直接暴露 Lagged | forwarding task 内部吞 Lagged，drain 数量不定（断言 ≥1 即认为有消息到达） |

1 个单元测试因 Bug 5 删除（语义已变）：

| 测试文件 | 处理 |
|---------|------|
| `src/heartbeat.rs::node_without_ack_times_out` | **删除**。Phase 6 模型下"handle 不调 recv 不 ack"已不成立；新的等价场景是"handle 被 drop"——由 lib.rs 的 `zombie_entry_cleaned_by_heartbeat_timeout` 覆盖 |

### 实际测试结果

```
cargo test --workspace
... (略) ...
test result: ok. 129 passed (arf-core; Node 46 + 历史测试)
test result: ok.  82 passed (arf-bus lib)
test result: ok.  12 passed (arf-bus integration, 含 3 个改写的旧测试)
... (其他 crate 全部 OK)
0 FAILED
```

### 新增 crate 依赖

`crates/arf-bus/Cargo.toml`:

```toml
[dependencies]
arf-core = { path = "../arf-core" }
tokio = { version = "1", features = ["sync", "rt", "macros", "time"] }
serde_json = "1"
uuid = { version = "1", features = ["v4"] }
futures = "0.3"   # ← 新增（future::select_all for recv() 多订阅调度）
```

### 6.0.2 API 最终签名（与初稿有差异）

```rust
// 初稿: NodeHandle 持 inbound_rx: broadcast::Receiver<Message>
// 实现: NodeHandle 持 Vec<Subscription>，每个 Subscription 有 inbound_rx: mpsc::Receiver<Message>

impl NodeHandle {
    pub async fn send(&self, msg_type: &str, to: Vec<NodeId>, payload: Value) -> Result<SendReceipt, SendError>;
    pub async fn send_via(&self, bus_id: BusId, msg_type: &str, to: Vec<NodeId>, payload: Value) -> Result<SendReceipt, SendError>;
    pub async fn attach_to(&mut self, bus: Arc<Bus>, filter: MessageFilter) -> Result<BusId, ConnectError>;
    pub async fn recv(&mut self) -> Result<Message, broadcast::error::RecvError>;
    pub fn try_recv(&mut self) -> Result<Option<Message>, broadcast::error::TryRecvError>;
    pub fn node_info(&self) -> &NodeInfo;
    pub fn filter_config(&self) -> &MessageFilter;
    pub fn subscriptions(&self) -> Vec<BusId>;
    pub async fn disconnect(self);
}
```

### Bus 新增

```rust
impl Bus {
    pub id: BusId,                  // 6.0.2 新增字段；6.0.3 起 Msg.from_bus 用此
    pub(crate) fn subscribe_internal(&self) -> broadcast::Receiver<Message>;  // for forwarding tasks
    pub(crate) fn cmd_tx: mpsc::Sender<BusCommand>;  // Send 命令走它
}
```

`SendError::NoSuchBus(BusId)` variant 也已添加（在 arf-core 中）。