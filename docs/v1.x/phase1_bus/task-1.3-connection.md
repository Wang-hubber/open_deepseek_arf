# 任务 1.3：节点连接与断连

> Phase 1 — Bus 消息总线第三项任务
> 父文档：`docs/v1.x/phase1/phase1-bus-design.md`
> 前置：任务 1.2 Bus 数据通道

## 设计思路

任务 1.3 在 Bus 数据通道之上叠加节点生命周期管理—连接时注册 `NodeEntry` + 广播 `node_online`，断连时清理 `NodeEntry` + 广播 `node_offline`。

**1.2 的 Bus 是纯通道**：`send()` → mpsc → 消息循环 → broadcast → `subscribe()`。没有"节点"概念——谁都可以 send，谁都可以 subscribe。

**1.3 引入节点概念**：

```
                 Bus
                 ┌──────────────────────────────┐
  connect() ────→│ register NodeEntry             │
                 │ broadcast node_online          │
                 │         ↓                      │
  NodeHandle ───→│ send(msg) → broadcast          │
                 │         ↓                      │
  NodeHandle ←───│ recv() ← broadcast_rx          │
                 │         ↓                      │
  disconnect() ─→│ remove NodeEntry               │
                 │ broadcast node_offline          │
                 └──────────────────────────────┘
```

**NodeHandle 封装了什么**：
- `cmd_tx` clone — 用于 `send()` 和 `disconnect()` 向消息循环发命令
- `broadcast_rx` — 订阅 Bus 广播，用于 `recv()`
- `info: NodeInfo` — 节点自身身份信息
- `filter: MessageFilter` — 接收侧过滤规则（1.6 实现过滤逻辑，1.3 仅存储）

**NodeHandle::send() 与 Bus::send() 的区别**：
- `Bus::send()` 是低级 API，传入完整 `Message`，用于内部（如消息循环自己广播 lifecycle 消息）
- `NodeHandle::send()` 是高级 API，传入 `(msg_type, to, payload)`，自动填充 `from` 和 `timestamp`，返回 `SendReceipt`

**任务 1.3 的范围**：

- `BusCommand::Connect` / `BusCommand::Disconnect` 变体
- `NodeEntry` 结构体 — 存储节点信息 + 心跳时间 + filter
- `Bus.nodes` — `Arc<RwLock<HashMap<NodeId, NodeEntry>>>` 在线节点表
- `Bus::connect()` — 创建 NodeHandle、注册 NodeEntry、广播 `node_online`
- `NodeHandle::send()` — 构造 Message 并通过 cmd_tx 发送，返回 `SendReceipt`
- `NodeHandle::recv()` / `try_recv()` — 从 broadcast_rx 接收（无过滤，1.6 加）
- `NodeHandle::disconnect()` — 通过 cmd_tx 发 Disconnect 指令，消息循环广播 `node_offline`

**不在 1.3 范围**：
- 心跳检测（1.4）
- `SendReceipt.matching_nodes` 精确计算（1.6 filter 匹配）
- `recv()` 的 `MessageFilter` 过滤逻辑（1.6）
- `Bus::graph()`（1.7）

---

## 架构变更

### Bus 字段新增

| 字段 | 类型 | 用途 |
|------|------|------|
| `nodes` | `Arc<RwLock<HashMap<NodeId, NodeEntry>>>` | 在线节点表，Bus 和消息循环共享 |

### BusCommand 新增变体

| 变体 | 携带数据 | 用途 |
|------|---------|------|
| `Connect` | `info: NodeInfo`, `filter: MessageFilter`, `respond_to: oneshot::Sender<Result<NodeHandle, ConnectError>>` | 注册节点 |
| `Disconnect` | `node_id: NodeId`, `respond_to: oneshot::Sender<()>` | 注销节点 |

### 消息循环变更

- 处理 `Connect`：注册 NodeEntry → broadcast `node_online` → 回复
- 处理 `Disconnect`：移除 NodeEntry → broadcast `node_offline` → 回复
- `Send` 变体响应类型从 `Result<(), SendError>` 升级为 `Result<SendReceipt, SendError>`

---

## 依赖

无新增依赖。`crates/arf-bus/Cargo.toml` 不变。

---

## 代码实现

### `crates/arf-bus/src/lib.rs`（修改）

在现有基础上修改以下部分：

#### 新增 import

```rust
use arf_core::{Message, NodeId, NodeInfo, SendError, SendReceipt, MessageFilter, BusGraph};
use std::collections::HashMap;
use std::sync::RwLock;
use std::sync::Arc;
```

逐行：
- `NodeInfo`、`SendReceipt`、`MessageFilter` — 任务 1.1 定义的类型，1.3 开始使用
- `HashMap` — 在线节点表的数据结构
- `RwLock` — 读写锁：多个 `send()` 可并发读 nodes 表，connect/disconnect 独占写

#### Bus 结构体新增 nodes 字段

```rust
pub struct Bus {
    cmd_tx: mpsc::Sender<BusCommand>,
    broadcast_tx: broadcast::Sender<Message>,
    /// Online nodes registry — shared with message loop.
    nodes: Arc<RwLock<HashMap<NodeId, NodeEntry>>>,
    message_count: Arc<AtomicU64>,
    start_time: Instant,
    _loop_handle: tokio::task::JoinHandle<()>,
}
```

逐行：
- `nodes: Arc<RwLock<HashMap<...>>>` — `Arc` 让 Bus 和消息循环共享同一份数据。`RwLock` 允许并发读（send 查在线数）和排他写（connect/disconnect 增删节点）

#### BusCommand 新增变体 + Send 响应类型升级

```rust
enum BusCommand {
    Send {
        msg: Message,
        respond_to: oneshot::Sender<Result<SendReceipt, SendError>>,
    },
    Connect {
        info: NodeInfo,
        filter: MessageFilter,
        respond_to: oneshot::Sender<Result<NodeHandle, ConnectError>>,
    },
    Disconnect {
        node_id: NodeId,
        respond_to: oneshot::Sender<()>,
    },
    Shutdown {
        respond_to: oneshot::Sender<()>,
    },
}
```

逐行：
- `Send` 的 `respond_to` 类型从 `Result<(), SendError>` 升级为 `Result<SendReceipt, SendError>`——消息循环现在有 nodes map，可以计算在线节点数
- `Connect` — 携带 `NodeInfo`（节点身份）和 `MessageFilter`（接收规则）。`respond_to` 返回 `NodeHandle`——连接成功后节点拿到的操作句柄。如果相同 NodeId 已在线则返回 `ConnectError::AlreadyConnected`
- `Disconnect` — 携带 `node_id` 标识要移除的节点。不需要错误类型因为重复断连只是 no-op

#### ConnectError 类型

```rust
/// Errors that can occur when connecting to the Bus.
#[derive(Debug)]
pub enum ConnectError {
    /// A node with this NodeId is already connected.
    AlreadyConnected(NodeId),
    /// Bus has been shut down.
    BusClosed,
}

impl std::fmt::Display for ConnectError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::AlreadyConnected(id) => write!(f, "node already connected: {id}"),
            Self::BusClosed => write!(f, "bus closed"),
        }
    }
}

impl std::error::Error for ConnectError {}
```

#### NodeEntry 结构体

```rust
/// Internal per-node state stored in the Bus nodes map.
pub(crate) struct NodeEntry {
    pub info: NodeInfo,
    /// Last heartbeat ack received. Used in task 1.4.
    pub last_ack: Instant,
    pub filter: MessageFilter,
}
```

逐行：
- `pub(crate)` — crate 内可见（lib.rs + connection.rs），外部不可访问
- `last_ack` — 为 1.4 心跳预留，connect 时设为当前时间

#### Bus::new() 新增 nodes 初始化

```rust
pub fn new(
    heartbeat_interval: Duration,
    heartbeat_timeout: Duration,
    channel_capacity: usize,
) -> Self {
    let (broadcast_tx, drain_rx) = broadcast::channel(channel_capacity);
    let (cmd_tx, cmd_rx) = mpsc::channel(256);
    let message_count = Arc::new(AtomicU64::new(0));
    let nodes = Arc::new(RwLock::new(HashMap::new()));

    let broadcast_tx_clone = broadcast_tx.clone();
    let count_clone = message_count.clone();
    let nodes_clone = nodes.clone();
    let loop_handle = tokio::spawn(async move {
        run_message_loop(cmd_rx, broadcast_tx_clone, drain_rx, count_clone, nodes_clone).await;
    });

    Self {
        cmd_tx,
        broadcast_tx,
        nodes,
        message_count,
        start_time: Instant::now(),
        _loop_handle: loop_handle,
    }
}
```

逐行：
- `nodes = Arc::new(RwLock::new(HashMap::new()))` — 空节点表
- `nodes_clone = nodes.clone()` — `Arc::clone()` 增加引用计数，传给消息循环

#### Bus::send() 返回类型更新

```rust
/// Send a message to be broadcast on the bus.
///
/// Returns `Ok(SendReceipt)` with online node counts.
/// Returns `Err(SendError::BusClosed)` if the bus has been shut down.
pub async fn send(&self, msg: Message) -> Result<SendReceipt, SendError> {
    let (tx, rx) = oneshot::channel();
    self.cmd_tx
        .send(BusCommand::Send {
            msg,
            respond_to: tx,
        })
        .await
        .map_err(|_| SendError::BusClosed)?;
    rx.await.map_err(|_| SendError::BusClosed)?
}
```

逐行：
- 返回类型从 `Result<(), SendError>` 改为 `Result<SendReceipt, SendError>`——这是 Breaking change，所有调用方和测试需要更新

#### 消息循环新增 nodes 参数 + Connect/Disconnect 处理

```rust
async fn run_message_loop(
    mut cmd_rx: mpsc::Receiver<BusCommand>,
    broadcast_tx: broadcast::Sender<Message>,
    mut drain_rx: broadcast::Receiver<Message>,
    message_count: Arc<AtomicU64>,
    nodes: Arc<RwLock<HashMap<NodeId, NodeEntry>>>,
) {
    while let Some(cmd) = cmd_rx.recv().await {
        match cmd {
            BusCommand::Send { msg, respond_to } => {
                let _ = broadcast_tx.send(msg);
                message_count.fetch_add(1, Ordering::Relaxed);
                while drain_rx.try_recv().is_ok() {}

                let online_nodes = nodes.read().unwrap().len();
                let receipt = SendReceipt {
                    message_id: msg.id,
                    online_nodes,
                    matching_nodes: online_nodes, // TODO: filter matching in 1.6
                };
                let _ = respond_to.send(Ok(receipt));
            }
            BusCommand::Connect {
                info,
                filter,
                respond_to,
            } => {
                let result = handle_connect(&broadcast_tx, &nodes, info, filter);
                let _ = respond_to.send(result);
            }
            BusCommand::Disconnect {
                node_id,
                respond_to,
            } => {
                handle_disconnect(&broadcast_tx, &nodes, &node_id);
                let _ = respond_to.send(());
            }
            BusCommand::Shutdown { respond_to } => {
                let _ = respond_to.send(());
                break;
            }
        }
    }
}
```

逐行：
- `nodes.read().unwrap().len()` — 读锁，获取当前在线节点数。poisoned lock 直接 unwrap panic（lock 被 panic 污染时宁 crash 不掩错）
- `matching_nodes: online_nodes` — 临时行为，1.6 实现 filter 匹配后改为精确计算
- `handle_connect()` — 独立函数，检查重复 → 注册 NodeEntry → broadcast `node_online` → 构造 NodeHandle
- `handle_disconnect()` — 独立函数，移除 NodeEntry → broadcast `node_offline`

#### handle_connect / handle_disconnect 辅助函数

```rust
fn handle_connect(
    broadcast_tx: &broadcast::Sender<Message>,
    nodes: &Arc<RwLock<HashMap<NodeId, NodeEntry>>>,
    info: NodeInfo,
    filter: MessageFilter,
) -> Result<NodeHandle, ConnectError> {
    let node_id = info.node_id.clone();

    // Check for duplicate
    {
        let map = nodes.read().unwrap();
        if map.contains_key(&node_id) {
            return Err(ConnectError::AlreadyConnected(node_id));
        }
    }

    // Register entry
    {
        let mut map = nodes.write().unwrap();
        map.insert(
            node_id.clone(),
            NodeEntry {
                info: info.clone(),
                last_ack: Instant::now(),
                filter: filter.clone(),
            },
        );
    }

    // Broadcast node_online
    let online_msg = Message::new(
        "node_online",
        node_id.clone(),
        None, // broadcast
        serde_json::to_value(&info).unwrap_or_default(),
    );
    let _ = broadcast_tx.send(online_msg);

    // Create NodeHandle — broadcast_rx is created by Bus::connect() caller
    // We receive it via the oneshot channel. Wait — we need the rx.
    // See below: NodeHandle construction requires broadcast_rx from Bus.
}
```

**架构决策**：`broadcast_rx` 由 `Bus::connect()` 调用 `bus.subscribe()` 创建，然后通过 `Connect` 命令传给消息循环……但这样 `oneshot::Sender` 需要传 `broadcast::Receiver`，而 `broadcast::Receiver` 不是 `Send` 的？实际上 `broadcast::Receiver<Message>` 是 `Send` 的（只要 `Message: Send`）。

**简化方案**：让 `Bus::connect()` 创建 broadcast receiver，然后把 receiver 通过 oneshot 传给消息循环，消息循环组装 NodeHandle 后通过 oneshot 返回。

实际上更简单：`Bus::connect()` 直接在调用方创建 receiver 和构造 NodeHandle，不需要经过消息循环。消息循环只需要：
1. 注册 NodeEntry
2. 广播 node_online
3. 返回确认（带注册信息或错误）

这样 `Connect` 命令的 respond_to 只需要返回 `Result<(), ConnectError>`，而 NodeHandle 的组装在 `Bus::connect()` 中完成：

```rust
// In impl Bus (connection.rs)
pub async fn connect(
    &self,
    info: NodeInfo,
    filter: MessageFilter,
) -> Result<NodeHandle, ConnectError> {
    let (tx, rx) = oneshot::channel();
    self.cmd_tx
        .send(BusCommand::Connect {
            info: info.clone(),
            filter: filter.clone(),
            respond_to: tx,
        })
        .await
        .map_err(|_| ConnectError::BusClosed)?;

    rx.await.map_err(|_| ConnectError::BusClosed)??;

    // Now the node is registered and node_online has been broadcast.
    // Create the NodeHandle.
    let broadcast_rx = self.broadcast_tx.subscribe();
    Ok(NodeHandle {
        cmd_tx: self.cmd_tx.clone(),
        broadcast_rx,
        info,
        filter,
    })
}
```

这样更清晰：消息循环只负责数据操作 + 广播，NodeHandle 构造在调用方。

---

### `crates/arf-bus/src/connection.rs`（新文件）

```rust
//! Node connection lifecycle — connect, send, receive, disconnect.
//!
//! `NodeHandle` is the primary API that nodes use to interact with the Bus.

use arf_core::{
    Message, MessageFilter, NodeId, NodeInfo, SendError, SendReceipt,
};
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::{broadcast, mpsc, oneshot};

use crate::{Bus, BusCommand, ConnectError, NodeEntry};

// ═══════════════════════════════════════════════════════════════════
// NodeHandle
// ═══════════════════════════════════════════════════════════════════

/// A connected node's handle to the Bus.
///
/// Created by `Bus::connect()`, consumed by `disconnect()`.
/// Used to send messages, receive filtered messages, and query node info.
pub struct NodeHandle {
    /// Clone of Bus.cmd_tx — sends commands to the message loop.
    pub(crate) cmd_tx: mpsc::Sender<BusCommand>,
    /// Subscription to the broadcast channel — receives all messages.
    pub(crate) broadcast_rx: broadcast::Receiver<Message>,
    /// This node's identity and capabilities.
    pub(crate) info: NodeInfo,
    /// Message filter for receive-side filtering (used in task 1.6).
    pub(crate) filter: MessageFilter,
}
```

逐行：
- `pub struct NodeHandle` — 对外暴露，节点通过它操作 Bus
- `pub(crate)` 字段 — crate 内可见（connection.rs ↔ lib.rs），外部只能通过公开方法访问
- `cmd_tx` — 发送消息和断连命令
- `broadcast_rx` — 接收广播消息
- `info` — 节点自身身份
- `filter` — 接收侧过滤（1.6 用）

```rust
impl NodeHandle {
    /// Send a message from this node.
    ///
    /// The `from` field is automatically filled from this node's `NodeInfo`.
    /// Returns a `SendReceipt` with online node counts.
    pub async fn send(
        &self,
        msg_type: &str,
        to: Option<NodeId>,
        payload: serde_json::Value,
    ) -> Result<SendReceipt, SendError> {
        let msg = Message::new(
            msg_type,
            self.info.node_id.clone(),
            to,
            payload,
        );
        self.send_raw(msg).await
    }

    /// Send a pre-constructed Message.
    ///
    /// The `from` field is NOT overridden — use with caution.
    /// Prefer `send()` for normal use.
    pub(crate) async fn send_raw(&self, msg: Message) -> Result<SendReceipt, SendError> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx
            .send(BusCommand::Send {
                msg,
                respond_to: tx,
            })
            .await
            .map_err(|_| SendError::BusClosed)?;
        rx.await.map_err(|_| SendError::BusClosed)?
    }
```

逐行：
- `send()` — 标准发送接口。`from` 自动从 `self.info.node_id` 填充，调用方只需关心 `msg_type`、`to`、`payload`
- `send_raw()` — `pub(crate)` 内部用（如 lifecycle 消息有特殊 from 需求），也用于测试
- `oneshot` 请求-响应：等待消息循环确认消息已广播
- 如果消息循环已退出（Bus shutdown），`cmd_tx.send()` 或 `rx.await` 返回 error

```rust
    /// Receive the next message that passes this node's filter.
    ///
    /// Blocks until a matching message arrives.
    /// In task 1.3, filtering is not yet implemented — all messages are returned.
    /// MessageFilter filtering will be added in task 1.6.
    pub async fn recv(&self) -> Result<Message, broadcast::error::RecvError> {
        // TODO: apply self.filter in task 1.6
        self.broadcast_rx.resubscribe().recv().await
    }

    /// Try to receive a message without blocking.
    ///
    /// Returns `Ok(Some(msg))` if a message is available,
    /// `Ok(None)` if no message is ready,
    /// `Err(Lagged(n))` if messages were lost.
    pub fn try_recv(&self) -> Result<Option<Message>, broadcast::error::TryRecvError> {
        // TODO: apply self.filter in task 1.6
        match self.broadcast_rx.try_recv() {
            Ok(msg) => Ok(Some(msg)),
            Err(broadcast::error::TryRecvError::Empty) => Ok(None),
            Err(broadcast::error::TryRecvError::Closed) => {
                Err(broadcast::error::TryRecvError::Closed)
            }
            Err(broadcast::error::TryRecvError::Lagged(n)) => {
                Err(broadcast::error::TryRecvError::Lagged(n))
            }
        }
    }
```

逐行：
- `recv()` — **直接用 `self.broadcast_rx` 而不是 `resubscribe()`**。等等，这里有个问题：`broadcast::Receiver::recv()` 需要 `&mut self`，但 `self` 是 `&self`。

**可变性问题**：`broadcast::Receiver::recv(&mut self)` 需要可变引用，但 NodeHandle 可能被多处共享。解决方案：
- `NodeHandle` 的 `recv()` 和 `try_recv()` 需要 `&mut self`
- 或者用 `Arc<Mutex<broadcast::Receiver>>` 包装
- 或者用 `RefCell`（单线程异步场景）

tokio broadcast receiver 设计上要求 `&mut self`（因为接收操作修改内部游标）。在异步场景中，通常每个节点只有一个 task 在调用 `recv()`，所以 `&mut self` 是合理的。调用方如需共享，自行 `Arc<Mutex<>>` 包装。

**修正**：`recv()` 和 `try_recv()` 签名改为 `&mut self`。

```rust
    /// Receive the next message (mutable reference required by broadcast::Receiver).
    pub async fn recv(&mut self) -> Result<Message, broadcast::error::RecvError> {
        self.broadcast_rx.recv().await
    }

    /// Try to receive without blocking (mutable reference required).
    pub fn try_recv(&mut self) -> Result<Option<Message>, broadcast::error::TryRecvError> {
        match self.broadcast_rx.try_recv() {
            Ok(msg) => Ok(Some(msg)),
            Err(broadcast::error::TryRecvError::Empty) => Ok(None),
            Err(e @ broadcast::error::TryRecvError::Lagged(_)) => Err(e),
            Err(broadcast::error::TryRecvError::Closed) => Err(broadcast::error::TryRecvError::Closed),
        }
    }
```

```rust
    /// Get this node's identity and capabilities.
    pub fn node_info(&self) -> &NodeInfo {
        &self.info
    }

    /// Get this node's filter configuration.
    pub fn filter_config(&self) -> &MessageFilter {
        &self.filter
    }

    /// Disconnect this node from the Bus.
    ///
    /// Sends a Disconnect command to the message loop,
    /// which removes the NodeEntry and broadcasts `node_offline`.
    /// Consumes `self` — the handle is no longer usable after this.
    pub async fn disconnect(self) {
        let (tx, rx) = oneshot::channel();
        let _ = self
            .cmd_tx
            .send(BusCommand::Disconnect {
                node_id: self.info.node_id.clone(),
                respond_to: tx,
            })
            .await;
        let _ = rx.await;
    }
}
```

逐行：
- `node_info()` — 只读访问节点信息
- `disconnect(self)` — 消费 `self`，断连后句柄不可再使用。发送 Disconnect 命令后等待消息循环确认，然后 drop self（包括 `broadcast_rx` 被 drop）
- disconnect 忽略 send/recv error——即使消息循环已退出，句柄的使命也是清理

---

### `impl Bus` — connect 方法（在 connection.rs）

```rust
impl Bus {
    /// Connect a node to the Bus.
    ///
    /// Registers the node in the online nodes map, broadcasts `node_online`,
    /// and returns a `NodeHandle` for the node to use.
    ///
    /// Returns `Err(ConnectError::AlreadyConnected)` if a node with the same
    /// `NodeId` is already online.
    pub async fn connect(
        &self,
        info: NodeInfo,
        filter: MessageFilter,
    ) -> Result<NodeHandle, ConnectError> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx
            .send(BusCommand::Connect {
                info: info.clone(),
                filter: filter.clone(),
                respond_to: tx,
            })
            .await
            .map_err(|_| ConnectError::BusClosed)?;

        // Wait for registration confirmation
        rx.await.map_err(|_| ConnectError::BusClosed)??;

        // Create the broadcast receiver AFTER registration,
        // so the node doesn't see its own node_online message.
        let broadcast_rx = self.broadcast_tx.subscribe();

        Ok(NodeHandle {
            cmd_tx: self.cmd_tx.clone(),
            broadcast_rx,
            info,
            filter,
        })
    }
}
```

逐行：
- `info.clone()` + `filter.clone()` — 两份数据：一份进入 NodeEntry（消息循环持有），一份进入 NodeHandle（节点持有）
- `rx.await` 的 `??` — 第一个 `?` 处理 oneshot 被 drop（BusClosed），第二个 `?` 处理 `ConnectError::AlreadyConnected`
- **broadcast_rx 在注册后创建**：`self.broadcast_tx.subscribe()` 只接收创建之后的广播消息。因为 `node_online` 已在注册时广播完毕，节点不会收到自己的 `node_online`。节点会收到后续所有消息，包括其他节点的 `node_online`
- `cmd_tx.clone()` — `mpsc::Sender` 是 `Clone` 的，每个 NodeHandle 持有一个独立的 sender clone，共享同一个 channel

---

## 变更影响分析

### 对已有代码的影响

| 位置 | 变更 | 原因 |
|------|------|------|
| `Bus::send()` 返回类型 | `Result<(), SendError>` → `Result<SendReceipt, SendError>` | 消息循环有 nodes map，可计算在线数 |
| `BusCommand::Send` respond_to | `oneshot::Sender<Result<(), SendError>>` → `oneshot::Sender<Result<SendReceipt, SendError>>` | 同上 |
| `run_message_loop` 签名 | 新增 `nodes: Arc<RwLock<HashMap<...>>>` | 消息循环需要读写节点表 |
| 测试中的 `bus.send(...)` | `.unwrap()` 返回值变为 `SendReceipt` | 适配新返回类型 |
| `bus.send(msg)` 所有调用 | 接收 `SendReceipt` 而非 `()` | 适配 |

### 不计入 1.3 的后续工作

| 事项 | 安排任务 |
|------|---------|
| `recv()` 中应用 MessageFilter 过滤 | 1.6 |
| `matching_nodes` 精确匹配 filter | 1.6 |
| 心跳超时检测 + `last_ack` 更新 | 1.4 |
| `Bus::graph()` 健康图 API | 1.7 |
| shutdown 时自动 disconnect 所有节点 | 1.4 |

---

## 单元测试

> 每个测试标注测试角度

### 测试分类

```
NodeHandle
├── 构造 & 基本属性 (4 tests)
│   ├── [构造] connect 返回 NodeHandle，node_info() 返回正确信息
│   ├── [构造] connect 后 node_online 被广播，所有已有 subscriber 收到
│   ├── [构造] filter_config() 返回传入的 MessageFilter
│   └── [错误] 重复 NodeId connect → AlreadyConnected
├── send & recv (5 tests)
│   ├── [数据] NodeHandle.send() → 其他 receiver 收到正确消息
│   ├── [数据] NodeHandle.send() → SendReceipt.online_nodes 正确
│   ├── [数据] NodeHandle.recv() 收到其他节点发送的消息
│   ├── [数据] try_recv() 无消息时返回 Ok(None)
│   └── [数据] 多条消息按顺序收到
├── disconnect (3 tests)
│   ├── [生命周期] disconnect() 后 node_offline 被广播
│   ├── [生命周期] disconnect 后 NodeEntry 从 Bus 移除
│   └── [生命周期] 重复 disconnect（同一 NodeId connect → disconnect → connect）成功
├── 多节点 (3 tests)
│   ├── [多节点] 两个节点互通——互相看到对方的 send
│   ├── [多节点] 新节点 connect 后看到后续消息，看不到历史消息
│   └── [多节点] 节点 disconnect 后其他节点不受影响
├── shutdown (2 tests)
│   ├── [关闭] shutdown 后 connect 返回 BusClosed
│   └── [关闭] shutdown 后 NodeHandle.send() 返回 BusClosed
└── 并发 (1 test)
    └── [并发] 多节点同时 connect+send 不丢消息
```

---

## 测试代码

### Rust 编译期约束解决

`NodeHandle.recv()` 需要 `&mut self`（broadcast::Receiver::recv 要求可变引用）。在测试中，多个 spawn 的 task 需要共享 NodeHandle 时，用 `Arc<tokio::sync::Mutex<NodeHandle>>` 包装。

测试辅助函数：

```rust
// In tests module of connection.rs

fn test_node_info(id: &str) -> NodeInfo {
    NodeInfo {
        node_id: NodeId::new(id),
        node_type: "test".into(),
        capabilities: serde_json::json!({}),
        online_since: 0, // filled by connect if needed
    }
}

fn test_filter() -> MessageFilter {
    MessageFilter {
        types: None, // accept all
        to_match: ToMatch::All,
    }
}

fn test_bus() -> Bus {
    Bus::new(
        Duration::from_secs(1),
        Duration::from_secs(3),
        16,
    )
}
```

### 测试用例

```rust
// ═══════════════════════════════════════════════════════════════
// NodeHandle — 构造 & 基本属性 (4 tests)
// ═══════════════════════════════════════════════════════════════

// [构造] connect 返回 NodeHandle，node_info() 返回传入的 NodeInfo
#[tokio::test]
async fn connect_returns_node_handle_with_correct_info() {
    let bus = test_bus();
    let info = test_node_info("node-1");
    let handle = bus.connect(info.clone(), test_filter()).await.unwrap();
    assert_eq!(handle.node_info().node_id.as_str(), "node-1");
    assert_eq!(handle.node_info().node_type, "test");
    handle.disconnect().await;
    bus.shutdown().await;
}

// [构造] connect 后 node_online 被广播
#[tokio::test]
async fn connect_broadcasts_node_online() {
    let bus = test_bus();

    // Subscribe BEFORE connect to capture node_online
    let mut rx = bus.subscribe();

    let info = test_node_info("node-a");
    let handle = bus.connect(info, test_filter()).await.unwrap();

    // rx should receive node_online
    let msg = rx.recv().await.unwrap();
    assert_eq!(msg.msg_type, "node_online");
    assert_eq!(msg.from.as_str(), "node-a");

    handle.disconnect().await;
    bus.shutdown().await;
}

// [构造] filter_config() 返回传入的 MessageFilter
#[tokio::test]
async fn node_handle_stores_filter_config() {
    let bus = test_bus();
    let filter = MessageFilter {
        types: Some(vec!["model_call".into()]),
        to_match: ToMatch::DirectedToMe,
    };
    let handle = bus.connect(test_node_info("n1"), filter.clone()).await.unwrap();
    assert_eq!(handle.filter_config().types, filter.types);
    handle.disconnect().await;
    bus.shutdown().await;
}

// [错误] 重复 NodeId connect → AlreadyConnected
#[tokio::test]
async fn connect_duplicate_node_id_returns_already_connected() {
    let bus = test_bus();
    let info = test_node_info("dup");
    let _h1 = bus.connect(info.clone(), test_filter()).await.unwrap();

    let result = bus.connect(info, test_filter()).await;
    assert!(matches!(result, Err(ConnectError::AlreadyConnected(_))));

    _h1.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════
// NodeHandle — send & recv (5 tests)
// ═══════════════════════════════════════════════════════════════

// [数据] NodeHandle.send() → 其他 subscriber 收到消息，from 自动填充
#[tokio::test]
async fn node_handle_send_message_appears_on_bus() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let sender = bus.connect(test_node_info("sender"), test_filter()).await.unwrap();
    let receipt = sender
        .send("action", None, serde_json::json!({"cmd": "run"}))
        .await
        .unwrap();

    let msg = rx.recv().await.unwrap();
    assert_eq!(msg.msg_type, "action");
    assert_eq!(msg.from.as_str(), "sender");
    assert_eq!(msg.payload, serde_json::json!({"cmd": "run"}));
    assert_eq!(msg.id, receipt.message_id);

    sender.disconnect().await;
    bus.shutdown().await;
}

// [数据] SendReceipt.online_nodes 反映在线节点数（不含发送者自身是否计入？——计入，因为节点也在 nodes map 中）
#[tokio::test]
async fn send_receipt_counts_online_nodes() {
    let bus = test_bus();
    let sender = bus.connect(test_node_info("s"), test_filter()).await.unwrap();

    // Only sender is online
    let receipt = sender.send("t", None, serde_json::json!(null)).await.unwrap();
    assert_eq!(receipt.online_nodes, 1);

    // Connect another node
    let other = bus.connect(test_node_info("o"), test_filter()).await.unwrap();
    let receipt2 = sender.send("t", None, serde_json::json!(null)).await.unwrap();
    assert_eq!(receipt2.online_nodes, 2);

    sender.disconnect().await;
    other.disconnect().await;
    bus.shutdown().await;
}

// [数据] NodeHandle.recv() 收到其他节点发送的消息
#[tokio::test]
async fn node_handle_recv_receives_from_others() {
    let bus = test_bus();
    let mut receiver = bus.connect(test_node_info("receiver"), test_filter()).await.unwrap();
    let sender = bus.connect(test_node_info("sender2"), test_filter()).await.unwrap();

    sender.send("ping", None, serde_json::json!("hello")).await.unwrap();

    let msg = receiver.recv().await.unwrap();
    // Note: receiver may see sender's node_online first
    // Drain until we see "ping"
    if msg.msg_type == "node_online" {
        let msg2 = receiver.recv().await.unwrap();
        assert_eq!(msg2.msg_type, "ping");
        assert_eq!(msg2.payload, serde_json::json!("hello"));
    } else {
        assert_eq!(msg.msg_type, "ping");
    }

    receiver.disconnect().await;
    sender.disconnect().await;
    bus.shutdown().await;
}

// [数据] try_recv() 无消息时返回 Ok(None)
#[tokio::test]
async fn try_recv_returns_none_when_empty() {
    let bus = test_bus();
    let mut handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();

    // Should be empty initially (broadcast_rx created after node_online)
    let result = handle.try_recv().unwrap();
    assert!(result.is_none());

    handle.disconnect().await;
    bus.shutdown().await;
}

// [数据] recv() 按发送顺序收到消息
#[tokio::test]
async fn recv_receives_messages_in_order() {
    let bus = test_bus();
    let mut receiver = bus.connect(test_node_info("recv"), test_filter()).await.unwrap();
    let sender = bus.connect(test_node_info("send"), test_filter()).await.unwrap();

    // Drain node_online messages
    let _ = receiver.recv().await.unwrap(); // sender's node_online

    for i in 0..5 {
        sender.send("seq", None, serde_json::json!(i)).await.unwrap();
    }

    for i in 0..5 {
        let msg = receiver.recv().await.unwrap();
        assert_eq!(msg.payload, serde_json::json!(i));
    }

    receiver.disconnect().await;
    sender.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════
// NodeHandle — disconnect (3 tests)
// ═══════════════════════════════════════════════════════════════

// [生命周期] disconnect() 后 node_offline 被广播
#[tokio::test]
async fn disconnect_broadcasts_node_offline() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let handle = bus.connect(test_node_info("ephemeral"), test_filter()).await.unwrap();

    // Drain node_online
    let _ = rx.recv().await.unwrap();

    handle.disconnect().await;

    let msg = rx.recv().await.unwrap();
    assert_eq!(msg.msg_type, "node_offline");
    assert_eq!(msg.from.as_str(), "ephemeral");

    bus.shutdown().await;
}

// [生命周期] disconnect 后同一 NodeId 可重新 connect（entry 已清理）
#[tokio::test]
async fn reconnect_after_disconnect_succeeds() {
    let bus = test_bus();
    let info = test_node_info("reconnector");

    let h1 = bus.connect(info.clone(), test_filter()).await.unwrap();
    h1.disconnect().await;

    // Reconnect with the same NodeId
    let h2 = bus.connect(info, test_filter()).await.unwrap();
    assert_eq!(h2.node_info().node_id.as_str(), "reconnector");

    h2.disconnect().await;
    bus.shutdown().await;
}

// [生命周期] disconnect 后 send 失败——NodeHandle 已消费
// 注：disconnect(self) 消费 NodeHandle，编译期保障无法再 send
// 此测试改为验证 disconnect → reconnect → send 正常
#[tokio::test]
async fn reconnect_send_works() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let info = test_node_info("r");

    let h1 = bus.connect(info.clone(), test_filter()).await.unwrap();
    h1.disconnect().await;

    // Drain node_online + node_offline
    let _ = rx.recv().await.unwrap();
    let _ = rx.recv().await.unwrap();

    let h2 = bus.connect(info, test_filter()).await.unwrap();
    h2.send("after_reconnect", None, serde_json::json!("ok")).await.unwrap();

    // Drain node_online then verify our message
    let _ = rx.recv().await.unwrap();
    let msg = rx.recv().await.unwrap();
    assert_eq!(msg.msg_type, "after_reconnect");

    h2.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════
// NodeHandle — 多节点 (3 tests)
// ═══════════════════════════════════════════════════════════════

// [多节点] 两个节点互通
#[tokio::test]
async fn two_nodes_can_exchange_messages() {
    let bus = test_bus();
    let mut a = bus.connect(test_node_info("A"), test_filter()).await.unwrap();
    let mut b = bus.connect(test_node_info("B"), test_filter()).await.unwrap();

    // Drain mutual node_online messages
    let _ = a.recv().await.unwrap(); // B's node_online
    let _ = b.recv().await.unwrap(); // A's node_online

    a.send("chat", None, serde_json::json!("from_a")).await.unwrap();
    b.send("chat", None, serde_json::json!("from_b")).await.unwrap();

    let a_rx = a.recv().await.unwrap();
    let b_rx = b.recv().await.unwrap();
    assert_eq!(b_rx.payload, serde_json::json!("from_a"));
    assert_eq!(a_rx.payload, serde_json::json!("from_b"));

    a.disconnect().await;
    b.disconnect().await;
    bus.shutdown().await;
}

// [多节点] 新节点看不到 connect 之前的历史消息（broadcast channel 是瞬时的）
#[tokio::test]
async fn late_joiner_sees_only_future_messages() {
    let bus = test_bus();
    let early = bus.connect(test_node_info("early"), test_filter()).await.unwrap();

    // Send before late joiner connects
    early.send("historical", None, serde_json::json!("ancient")).await.unwrap();

    let mut late = bus.connect(test_node_info("late"), test_filter()).await.unwrap();

    // late should see early's node_online, but NOT "historical"
    let msg = late.recv().await.unwrap();
    assert_eq!(msg.msg_type, "node_online");
    assert_eq!(msg.from.as_str(), "early");

    // Now send — late should see it
    early.send("current", None, serde_json::json!("now")).await.unwrap();
    let msg2 = late.recv().await.unwrap();
    assert_eq!(msg2.msg_type, "current");

    early.disconnect().await;
    late.disconnect().await;
    bus.shutdown().await;
}

// [多节点] 节点 disconnect 后其他节点不受影响
#[tokio::test]
async fn surviving_node_unaffected_by_other_disconnect() {
    let bus = test_bus();
    let survivor = bus.connect(test_node_info("survivor"), test_filter()).await.unwrap();
    let leaver = bus.connect(test_node_info("leaver"), test_filter()).await.unwrap();

    leaver.disconnect().await;

    // Survivor can still send
    let receipt = survivor.send("still_here", None, serde_json::json!("ok")).await.unwrap();
    assert_eq!(receipt.online_nodes, 1); // only survivor remains
    assert!(receipt.online_nodes > 0);

    survivor.disconnect().await;
    bus.shutdown().await;
}

// ═══════════════════════════════════════════════════════════════
// NodeHandle — shutdown 交互 (2 tests)
// ═══════════════════════════════════════════════════════════════

// [关闭] shutdown 后 connect 返回 BusClosed
#[tokio::test]
async fn connect_after_shutdown_returns_bus_closed() {
    let bus = test_bus();
    bus.shutdown().await;

    let result = bus.connect(test_node_info("too_late"), test_filter()).await;
    assert!(matches!(result, Err(ConnectError::BusClosed)));
}

// [关闭] shutdown 后 NodeHandle.send() 返回 BusClosed
#[tokio::test]
async fn node_handle_send_after_shutdown_returns_bus_closed() {
    let bus = test_bus();
    let handle = bus.connect(test_node_info("n"), test_filter()).await.unwrap();

    // Don't disconnect — just shutdown the bus
    bus.shutdown().await;

    let result = handle.send("t", None, serde_json::json!(null)).await;
    assert!(matches!(result, Err(SendError::BusClosed)));
}

// ═══════════════════════════════════════════════════════════════
// NodeHandle — 并发 (1 test)
// ═══════════════════════════════════════════════════════════════

// [并发] 多个节点同时 connect + send，消息全部送达
#[tokio::test]
async fn concurrent_connect_and_send_no_lost_messages() {
    let bus = Arc::new(test_bus());
    let mut rx = bus.subscribe();

    let handles: Vec<_> = (0..5)
        .map(|i| {
            let bus = bus.clone();
            tokio::spawn(async move {
                let info = test_node_info(&format!("node-{i}"));
                let h = bus.connect(info, test_filter()).await.unwrap();
                h.send("ping", None, serde_json::json!(i)).await.unwrap();
                h
            })
        })
        .collect();

    let mut node_handles = Vec::new();
    for h in handles {
        node_handles.push(h.await.unwrap());
    }

    // Count messages — 5 node_online + 5 ping = 10
    let mut ping_count = 0;
    let mut online_count = 0;
    for _ in 0..10 {
        let msg = rx.recv().await.unwrap();
        match msg.msg_type.as_str() {
            "node_online" => online_count += 1,
            "ping" => ping_count += 1,
            _ => {}
        }
    }
    assert_eq!(online_count, 5);
    assert_eq!(ping_count, 5);

    for h in node_handles {
        h.disconnect().await;
    }

    let bus = Arc::into_inner(bus).unwrap();
    bus.shutdown().await;
}
```

---

## 测试清单

| # | 角度 | 测试名 | 覆盖 |
|---|------|--------|------|
| 1 | `[构造]` | `connect_returns_node_handle_with_correct_info` | connect 返回正确 NodeHandle |
| 2 | `[构造]` | `connect_broadcasts_node_online` | node_online 广播 |
| 3 | `[构造]` | `node_handle_stores_filter_config` | filter_config 存储 |
| 4 | `[错误]` | `connect_duplicate_node_id_returns_already_connected` | AlreadyConnected 错误 |
| 5 | `[数据]` | `node_handle_send_message_appears_on_bus` | NodeHandle.send() → bus |
| 6 | `[数据]` | `send_receipt_counts_online_nodes` | SendReceipt.online_nodes |
| 7 | `[数据]` | `node_handle_recv_receives_from_others` | NodeHandle.recv() |
| 8 | `[数据]` | `try_recv_returns_none_when_empty` | try_recv() 空返回 |
| 9 | `[数据]` | `recv_receives_messages_in_order` | 消息顺序 |
| 10 | `[生命周期]` | `disconnect_broadcasts_node_offline` | node_offline 广播 |
| 11 | `[生命周期]` | `reconnect_after_disconnect_succeeds` | disconnect→reconnect |
| 12 | `[生命周期]` | `reconnect_send_works` | reconnect 后正常收发 |
| 13 | `[多节点]` | `two_nodes_can_exchange_messages` | 两节点互通 |
| 14 | `[多节点]` | `late_joiner_sees_only_future_messages` | 新节点不见历史 |
| 15 | `[多节点]` | `surviving_node_unaffected_by_other_disconnect` | 节点独立 |
| 16 | `[关闭]` | `connect_after_shutdown_returns_bus_closed` | shutdown→connect 拒绝 |
| 17 | `[关闭]` | `node_handle_send_after_shutdown_returns_bus_closed` | shutdown→send 拒绝 |
| 18 | `[并发]` | `concurrent_connect_and_send_no_lost_messages` | 5 并发不丢消息 |

---

## 对已有测试的调整

### lib.rs 测试

`Bus::send()` 返回类型从 `Result<(), SendError>` 改为 `Result<SendReceipt, SendError>`，所有 `.unwrap()` 调用不再需要修改（返回值只是从 `()` 变成 `SendReceipt`，忽略即可）。但需要确认 `message_id` 字段在需要验证的测试中一致。

具体影响：
- `send_and_receive_single_message`：`bus.send(msg).await.unwrap()` → 返回值变为 `SendReceipt`，已有的 `msg.id` 验证不变
- 所有 `bus.send(...).await.unwrap()` 调用——不检查返回值，直接忽略 `SendReceipt`，无需修改
- `directed_message_broadcast_to_all` — 同上
- 断言 `is_ok()` 的测试 — 不变

**无需修改已有测试的断言逻辑**，因为 `SendReceipt` 被 `unwrap()` 直接丢弃。编译通过即可。

---

## 小结

- **NodeHandle** 封装 cmd_tx clone + broadcast_rx + NodeInfo + MessageFilter，是节点操作 Bus 的唯一入口
- **connect()** 先注册 NodeEntry → 广播 node_online → 然后创建 broadcast_rx（确保节点看不到自己的 node_online）
- **disconnect()** 消费 NodeHandle，消息循环移除 NodeEntry + 广播 node_offline
- **消息循环新增 Connect/Disconnect 处理**，nodes map 在 Bus 和消息循环间 Arc 共享
- **Bus::send() 返回类型升级**为 `Result<SendReceipt, SendError>`，online_nodes 从 nodes map 读计数
- **18 个新测试**：4 构造 + 5 收发 + 3 生命周期 + 3 多节点 + 2 shutdown + 1 并发
