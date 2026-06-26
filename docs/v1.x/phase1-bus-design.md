# Phase 1 — Bus 消息总线设计

> CAN 总线模型：单通道广播 + 接收侧过滤 + Bus 驱动心跳 + 帧级投递保证
> 实现语言：Rust (crates/arf-core + crates/arf-bus)，PyO3 绑定暴露给 Python

## 设计决策

### 为什么是 CAN 模型？

| CAN 物理层 | ARF 逻辑层 |
|-----------|-----------|
| 单根线缆 | 一条 `tokio::sync::broadcast` channel |
| CAN ID Mask 硬件过滤 | `MessageFilter` 接收侧过滤 |
| 帧级 ACK（至少有人收到） | `SendReceipt`：在线节点数 + 目标节点在线性 |
| 错误帧全体丢弃 | `Lagged(n)` — 慢消费者自己兜底 |
| 优先级仲裁（ID 越小越优先） | 不需要（tokio 无冲突） |

- **无定向 drain 语义**：所有消息广播，节点自己过滤是否处理
- **无中心路由决策**：Bus 只做广播，不感知谁该收到什么
- **零偏袒**：Trace 与其他节点同一条通道同一种 I/O 条件，慢了就 `Lagged`

### 为什么是 broadcast channel 环形缓冲区？

- 消息瞬态：所有 receiver 读过后自动覆盖，无需显式 drain
- Lag 检测：`tokio::sync::broadcast::Receiver::recv()` 的 `Lagged(n)` 告知丢失量
- 容量可配置：通过 `channel_capacity` 匹配预期吞吐

---

## 共享类型（`crates/arf-core`）

```rust
// crates/arf-core/src/lib.rs

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::time::Duration;
use uuid::Uuid;

/// 消息唯一标识
#[derive(Debug, Clone, Hash, Eq, PartialEq, Serialize, Deserialize)]
pub struct NodeId(pub String);

/// 消息格式
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub id: Uuid,              // 消息唯一 ID
    pub msg_type: String,      // e.g. "node_online", "model_call", "action"
    pub from: NodeId,          // 发送者
    pub to: Option<NodeId>,    // None = 广播, Some = 定向（接收侧过滤用）
    pub payload: Value,        // serde_json::Value
    pub timestamp: u64,        // Unix 毫秒
}

/// 节点上线时广播的信息（存入在线图）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeInfo {
    pub node_id: NodeId,
    pub node_type: String,     // "engine" | "mcp" | "model" | "trace"
    pub capabilities: Value,   // { "tools": [...], "session_id": "s1", ... }
    pub online_since: u64,
}

/// Bus 健康图
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BusGraph {
    pub nodes: Vec<NodeInfo>,
    pub message_count: u64,
    pub uptime_ms: u64,
}

/// 发送确认
#[derive(Debug, Clone)]
pub struct SendReceipt {
    pub message_id: Uuid,
    pub online_nodes: usize,
    pub matching_nodes: usize,  // 可能匹配 filter 的在线节点数
}

/// 消息过滤器
#[derive(Debug, Clone)]
pub struct MessageFilter {
    pub types: Option<Vec<String>>,
    pub to_match: ToMatch,
}

#[derive(Debug, Clone)]
pub enum ToMatch {
    All,                       // 广播 + 定向全部收到
    BroadcastOnly,             // 只收 to=None
    DirectedToMe,              // 只收定向到自己的
    BroadcastAndDirectedToMe,  // 默认
}

/// 发送错误
#[derive(Debug)]
pub enum SendError {
    NodeOffline(NodeId),       // 定向目标不在线
    BusFull,                   // 缓冲区满
    BusClosed,                 // Bus 已关闭
}
```

---

## Bus API（`crates/arf-bus`）

```rust
// crates/arf-bus/src/lib.rs

impl Bus {
    /// 创建 Bus，内部启动心跳定时器和消息循环
    pub fn new(
        heartbeat_interval: Duration,
        heartbeat_timeout: Duration,
        channel_capacity: usize,
    ) -> Self;

    /// 节点接入 Bus
    pub async fn connect(&self, info: NodeInfo, filter: MessageFilter) -> Result<NodeHandle>;

    /// 查询健康图
    pub fn graph(&self) -> BusGraph;

    /// 关闭 Bus
    pub async fn shutdown(&self);
}

impl NodeHandle {
    /// 发送消息
    pub async fn send(
        &self,
        msg_type: &str,
        to: Option<NodeId>,
        payload: Value,
    ) -> Result<SendReceipt, SendError>;

    /// 接收过滤后的下一条消息（阻塞等待）
    pub async fn recv(&self) -> Result<Message>;

    /// 尝试接收（不阻塞）
    pub fn try_recv(&self) -> Result<Option<Message>>;

    /// 节点信息
    pub fn node_info(&self) -> &NodeInfo;

    /// 断开连接（广播 node_offline）
    pub async fn disconnect(self);
}
```

---

## Bus 内部架构

```
                    incoming_tx (mpsc)
        节点 ─────────────────────→ Bus
         ↑                           │
         │                           │ 验证消息 → broadcast_tx.send()
         │                           │
         └── broadcast_rx ──────────┘  (广播给所有节点)
              (接收 + 过滤)
```

```rust
// Bus 内部结构
struct BusInner {
    incoming_tx: mpsc::Sender<BusCommand>,
    broadcast_tx: broadcast::Sender<Message>,
    nodes: RwLock<HashMap<NodeId, NodeEntry>>,
    heartbeat_interval: Duration,
    heartbeat_timeout: Duration,
    message_count: AtomicU64,
    start_time: Instant,
}

enum BusCommand {
    Send {
        msg: Message,
        respond_to: oneshot::Sender<Result<SendReceipt, SendError>>,
    },
    Connect {
        info: NodeInfo,
        filter: MessageFilter,
        respond_to: oneshot::Sender<Result<NodeHandle>>,
    },
    // 心跳 ACK（节点内部应答，不暴露给应用层）
    HeartbeatAck {
        node_id: NodeId,
    },
}

struct NodeEntry {
    info: NodeInfo,
    last_ack: Instant,
    filter: MessageFilter,
}
```

## 心跳流程

```
Bus                              节点
 │── heartbeat_request ───────→│  (Bus 定时器驱动)
 │                                ├─ recv() 收到 heartbeat_request
 │←──────── ack ───────────────│  (NodeHandle 内部自动应答)
 │                                │
 │── heartbeat_request ───────→│  (下一轮)
 │         ... 超时 ...          │
 │                                │
 │ 标记 node offline             │
 │ broadcast node_offline ──→ 全员 │
```

- Bus 定时向所有在线节点广播 `msg_type="heartbeat_request"`
- 节点 `recv()` 返回后，`NodeHandle` 内部自动发送 `HeartbeatAck` 回 Bus
- Bus 在 `heartbeat_timeout` 内未收到 ack → 标记 offline → 广播 `node_offline`
- 节点应用层无感知——`send()`/`recv()` 正常工作

## 消息生命周期（完整链路）

```
节点 A                Bus                    节点 B        trace
  │                    │                       │            │
  ├─ connect(info) ──→│                       │            │
  │                    ├─ broadcast ──────────→├───────────→│
  │                    │   node_online         │            │
  │                    │                       │            │
  ├─ send("action", ─→│                       │            │
  │   None, {...})    ├─ broadcast ──────────→├───────────→│
  │←─ SendReceipt     │   action              │ (过滤通过)  │ (全收)
  │   {mid, nodes:3}  │                       │            │
  │                    │                       │            │
  ├─ send("tool",   ─→│                       │            │
  │   Some(mcp/fs),   ├─ broadcast ──────────→├───────────→│
  │   {...})          │   tool_call           │ (过滤通过)  │ (全收)
  │                    │                       │            │
  │                    ├─ heartbeat ──────────→├───────────→│
  │                    │   request             │ (内部 ACK)  │
  │                    │         ...           │            │
  │                    │   节点 B 超时         │            │
  │                    ├─ broadcast ──────────→├───────────→│
  │                    │   node_offline(B)     │            │
  │                    │                       │            │
  ├─ disconnect() ────→│                       │            │
  │                    ├─ broadcast ──────────→全员         │
  │                    │   node_offline(A)     │            │
```

---

## PyO3 绑定

```python
import arf

# 创建 Bus
bus = arf.Bus(
    heartbeat_interval_ms=5000,
    heartbeat_timeout_ms=15000,
    channel_capacity=1024,
)

# 创建节点
node = arf.NodeInfo("engine/main", "engine", {"session_id": "s1"})
filter = arf.MessageFilter(
    types=["model_response", "action_result", "heartbeat_request"],
    to_match="BroadcastAndDirectedToMe",
)
handle = bus.connect(node, filter)

# 发送消息
receipt = handle.send("model_call", None, {"prompt": "hello"})
print(f"msg={receipt.message_id}, online_nodes={receipt.online_nodes}")

# 接收消息
msg = handle.recv()
print(f"type={msg.msg_type}, from={msg.from}, payload={msg.payload}")

# Bus 健康图
graph = bus.graph()
for n in graph.nodes:
    print(f"{n.node_id} ({n.node_type})")

# 断开
handle.disconnect()
```

---

## 任务拆解（Phase 1）

| # | 任务 | 内容 | 产出 |
|---|------|------|------|
| 1.1 | `arf-core` 共享类型 | `NodeId`, `Message`, `NodeInfo`, `BusGraph`, `SendReceipt`, `MessageFilter`, `ToMatch`, `SendError` 类型定义 + Serialize/Deserialize | `crates/arf-core/src/lib.rs`（替换占位） |
| 1.2 | Bus 数据通道 | `BusInner` 结构 + `incoming_tx`/`broadcast_tx` + 消息循环（收→验→广播）+ `channel_capacity` 环形缓冲区 | `crates/arf-bus/src/bus_core.rs` |
| 1.3 | 节点连接与断连 | `connect()` → 注册 `NodeEntry` + 广播 `node_online`; `disconnect()` → 广播 `node_offline` + 清理 `NodeEntry` | `crates/arf-bus/src/connection.rs` |
| 1.4 | 心跳检测 | Bus 定时广播 `heartbeat_request`，节点内部自动 ACK，超时标记 offline + 广播 `node_offline` | `crates/arf-bus/src/heartbeat.rs` |
| 1.5 | 发送方投递保证 | `send()` 返回 `SendReceipt`（在线节点数 + 目标在线性验证）+ `SendError` 错误类型 | 同上，集成到消息循环 |
| 1.6 | 接收侧过滤 | `NodeHandle.recv()` 内部执行 `MessageFilter` 过滤，只返回匹配的消息 | `crates/arf-bus/src/filter.rs` |
| 1.7 | 健康图 | `Bus::graph()` 返回 `BusGraph`（节点列表 + 消息计数 + 运行时间） | `crates/arf-bus/src/graph.rs` |
| 1.8 | 单测 | 每个模块的 `#[cfg(test)]` 单元测试：消息收发、过滤、心跳超时、连接断连、错误路径 | 各 `src/*.rs` 的 `#[cfg(test)] mod tests` |
| 1.9 | 集成测试 | 多节点场景：上线/下线广播、定向消息过滤、心跳超时检测、trace 全量消费、lag 处理 | `crates/arf-bus/tests/integration.rs` |
| 1.10 | PyO3 绑定 | `py-arf/src/lib.rs` 暴露 `Bus`, `NodeInfo`, `MessageFilter`, `NodeHandle` 到 Python | `py-arf/src/lib.rs` + `py-arf/python/arf/__init__.py` |
| 1.11 | Python 测试 | pytest 验证 Python API：创建 Bus、连接、收发、图查询、断连 | `py-arf/tests/test_bus.py` |
| 1.12 | 文档与示例 | Phase 1 设计文档 + 教学示例 `phase1_bus_hello.py` | `docs/v1.x/phase1-bus-design.md` + `py-arf/python/arf/examples/phase1_bus_hello.py` |
| 1.13 | CI 验证 | `make ci`（cargo fmt + clippy + test + pytest）通过 | CI 绿灯 |
