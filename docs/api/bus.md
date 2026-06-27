# ARF Bus — 消息总线 API 参考

> **Phase 1** · CAN 总线模型 · 单通道广播 + 接收侧过滤
>
> `pip install arf` · `from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch`

---

## 概述

`arf.Bus` 是 ARF 框架的消息总线，采用 CAN 总线模型：**单通道广播 + 接收侧过滤**。所有消息通过一条 `tokio::sync::broadcast` 通道发送，每个节点根据自己的 `MessageFilter` 在本地决定接收哪些消息。Bus 本身不感知路由逻辑——谁该收到什么完全由接收侧决定。

```
                    ┌─────────────────────────────────┐
                    │           Bus (广播通道)          │
                    │  ┌─────────────────────────────┐ │
  engine/main ──────┼──┤  tokio broadcast channel    ├──┼────── trace/obs
                    │  │  (环形缓冲区, 可配容量)      │  │
  mcp/fs ───────────┼──┤                             ├──┼────── worker/1
                    │  └─────────────────────────────┘  │
  model/gpu-0 ──────┼───────────────────────────────────┼────── worker/2
                    │   节点 A 发出 → 全员收到            │
                    │   每个节点自行过滤                  │
                    └─────────────────────────────────┘
```

### CAN 总线模型

| CAN 物理层 | ARF Bus |
|-----------|---------|
| 单根线缆，所有节点并联 | 一条 `tokio::sync::broadcast` channel |
| CAN ID Mask 硬件过滤 | `MessageFilter` — 接收侧按 `type` + `to` 字段过滤 |
| 帧级 ACK（至少有人收到） | `SendReceipt` — 返回在线节点数 + 匹配节点数 |
| 错误帧全体丢弃 | `Lagged(n)` — 慢消费者自己兜底，不反压发送方 |
| 无中心路由 | Bus 只负责广播，不感知谁该收到什么 |

### 适用场景

**适合**：多节点协作（engine + mcp + model + trace）、广播/定向混合通信、节点动态上下线、需要心跳存活检测、一对多消息分发。

**不适合**：点对点 RPC（用 `jrpc`）、持久化消息队列（用外部 MQ）、需要 Exactly-Once 投递保证的金融交易。

---

## 快速上手

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

async def main():
    # 1. 创建 Bus
    bus = Bus(
        heartbeat_interval_ms=5000,   # 心跳间隔（毫秒）
        heartbeat_timeout_ms=15000,   # 心跳超时（毫秒）
        channel_capacity=64,          # 环形缓冲区容量
    )

    # 2. 连接节点
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {"role": "orchestrator"}),
        MessageFilter(),
    )
    worker = await bus.connect(
        NodeInfo("worker/1", "worker", {"gpu": 0}),
        MessageFilter(types=["job"]),
    )

    # 3. 广播消息
    receipt = await engine.send("job", [], {"task": "train", "lr": 0.001})
    print(f"已发送 → online={receipt.online_nodes}, matching={receipt.matching_nodes}")

    # 4. 接收消息
    msg = await worker.recv()
    print(f"收到 ← type={msg.msg_type}, payload={msg.payload}")

    # 5. 关闭 Bus
    await bus.shutdown()

asyncio.run(main())
```

**预期输出：**

```
已发送 → online=2, matching=2
收到 ← type=job, payload={'task': 'train', 'lr': 0.001}
```

### 安装

```bash
cd py-arf && ../.venv/bin/python -m maturin develop
```

```python
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch
print(__version__)  # "1.0.0-alpha.0"
```

---

## 核心概念

### 节点生命周期

一个节点在 Bus 上的完整生命周期：

```
connect() → 在线 → [send/recv] → disconnect() → 下线
                ↓                        ↓
           心跳循环              node_offline 广播
                ↓
      崩溃 (未调用 disconnect) → zombie entry → 心跳超时 → 清理
```

**Zombie entry（僵尸条目）**：如果 NodeHandle 在 Python 侧被 GC 回收时未调用 `disconnect()`（例如进程崩溃、忘记 await），NodeEntry 残留为 zombie。心跳超时后自动清理。清理前，同 NodeId 无法重连。

### 消息流转

```
  sender.send("job", [], {...})
       │
       ▼
  Bus: serde_json → Message { id: UUID, from: "engine/s", ... }
       │
       ▼
  broadcast_tx.send() ──→ 环形缓冲区 ──→ broadcast_rx (node A)
                                       ├─→ broadcast_rx (node B)
                                       └─→ broadcast_rx (node C)
                                              │
                                    recv() → filter.matches()?
                                              │ 通过
                                              ▼
                                         返回 Message
```

### 过滤器语义

`MessageFilter` 是**接收侧过滤**——消息总是全员广播，各节点自行决定是否接收。

```python
# 默认：接收所有类型，广播 + 定向到自己的都收
MessageFilter()

# 只收 action 和 event 类型
MessageFilter(types=["action", "event"])

# 会话绑定节点 — 只接收专门定向给自己的消息
MessageFilter(to_match=ToMatch.DirectedToMe)

# Trace 节点 — 全量消费（包括定向给别人的）
MessageFilter(types=None, to_match=ToMatch.All)
```

### `recv()` 执行模型

`recv()` 是 FIFO 阻塞接收，内部流程如下：

1. 调用 `broadcast_rx.recv()` 获取下一条消息
2. 如果是 `heartbeat_request` → 自动回复 ACK，**不返回给调用方**，回到步骤 1
3. 运行 `MessageFilter.matches(msg)` — 先检查类型白名单，再检查 to_match 策略
4. 匹配 → 返回 `Message`；不匹配 → 回到步骤 1

`try_recv()` 逻辑相同但不阻塞——无消息时返回 `None`。

!!! warning "并发 recv"
    同一 NodeHandle 同时只能有一个 `recv()` 或 `try_recv()` 在执行。并发调用抛 `RuntimeError("concurrent recv in progress")`。

### `recv()` vs `try_recv()` 详解

两者执行**完全相同的内部逻辑**（heartbeat 过滤 → filter 匹配），区别只在调用方式和等待行为。

| 维度 | `recv()` | `try_recv()` |
|------|----------|--------------|
| 调用方式 | `async` — 必须 `await` | **同步** — 直接调用 |
| 等待行为 | **阻塞**直到有匹配消息或 channel 关闭 | **立即返回**，无消息返回 `None` |
| 返回值 | `Message` | `Message \| None` |
| PyO3 实现 | Rust `async fn` → Python `Future` | Rust `fn` → Python 同步方法 |
| 并发限制 | 同一 Handle 同时只能一个在执行 | 与 `recv()` 互斥，但 `try_recv()` 之间不互斥 |

**`recv()` — 阻塞等待，适合主循环：**

```python
# 典型的主循环模式：一直等待，有消息就处理
async def event_loop(handle):
    while True:
        try:
            msg = await handle.recv()  # 阻塞，不消耗 CPU
            await process(msg)
        except Exception:
            break  # channel 关闭或 disconnect
```

**`try_recv()` — 非阻塞轮询，适合周期性检查：**

```python
# 边干其他事边检查有没有新消息
async def poll_and_work(handle):
    while True:
        msg = handle.try_recv()        # 不阻塞，立即返回
        if msg is not None:
            await process(msg)
        else:
            await do_other_work()       # 没消息时干别的事
            await asyncio.sleep(0.1)
```

**`try_recv()` — drain 模式，一次性清空缓冲区：**

```python
# 一次性读取所有待处理消息（例如 shutdown 后、初始化完成时）
def drain_all(handle):
    messages = []
    while True:
        msg = handle.try_recv()
        if msg is None:
            break
        messages.append(msg)
    return messages

msgs = drain_all(handle)
print(f"缓冲区中有 {len(msgs)} 条待处理消息")
```

**选择指南：**

| 场景 | 推荐 | 原因 |
|------|------|------|
| 主事件循环，消息驱动 | `recv()` | 阻塞等待不浪费 CPU，消息到来立即响应 |
| 定时轮询，兼顾其他任务 | `try_recv()` | 无消息时可以做其他工作 |
| shutdown 后清空缓冲区 | `try_recv()` | 同步、快速 drain，不阻塞 |
| 初始化后跳过 lifecycle | `recv()` 或 `try_recv()` | 取决于是否需要等待第一条应用消息 |
| 多路复用多个 Handle | `try_recv()` + `asyncio.sleep` | 轮流检查每个 Handle，简单但不如 `asyncio.wait` |

**`try_recv()` 的并发优势：** `try_recv()` 之间不互斥——可以连续多次调用而不必等待上一次完成。`recv()` 会持有一个内部锁直到消息返回，所以同一时刻只能有一个在执行。

### `message_count` 语义

`Bus.message_count` 和 `BusGraph.message_count` 只统计**应用消息**（通过 `NodeHandle.send()` 发送）。`node_online`、`node_offline`、`heartbeat_request` 等 lifecycle 消息不计入。

---

## API 参考

### `Bus`

```python
class Bus:
    def __init__(
        self,
        heartbeat_interval_ms: int = 1000,
        heartbeat_timeout_ms: int = 3000,
        channel_capacity: int = 16,
    ) -> None:
        ...
```

基于 CAN 模型的消息总线。所有消息全员广播，各节点通过 `MessageFilter` 本地过滤。

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|-----------|------|---------|-------------|
| `heartbeat_interval_ms` | `int` | `1000` | Bus 发送心跳请求的间隔。越短故障检测越快，但系统开销越大。 |
| `heartbeat_timeout_ms` | `int` | `3000` | 未收到 ACK 多久后节点被移出。实际使用中应 > `heartbeat_interval_ms` × 2。 |
| `channel_capacity` | `int` | `16` | 广播环形缓冲区大小。超出容量会导致慢消费者出现 `Lagged(n)`。高吞吐场景建议 256 以上。 |

**异常：** 无（构造函数不会失败）。

**示例：**

```python
# 默认配置：适合大多数场景
bus = Bus()

# 低延迟场景：快速心跳，小缓冲区
bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=300, channel_capacity=16)

# 高吞吐场景：大缓冲区应对突发流量
bus = Bus(heartbeat_interval_ms=5000, heartbeat_timeout_ms=15000, channel_capacity=1024)
```

---

#### `Bus.connect()`

```python
async def connect(
    self,
    info: NodeInfo,
    filter: MessageFilter,
) -> NodeHandle:
    ...
```

向 Bus 注册一个节点。广播 `node_online` 给所有已有节点，然后创建该节点的 broadcast receiver。

**参数：**

| 参数 | 类型 | 说明 |
|-----------|------|-------------|
| `info` | `NodeInfo` | 节点身份和能力。`node_id` 必须在当前在线节点中唯一。 |
| `filter` | `MessageFilter` | 控制该节点接收哪些消息。默认为 `MessageFilter()`（所有类型 + 广播和定向）。 |

**返回：** `NodeHandle` — 用于收发消息的节点句柄。

**异常：**

| 异常类型 | match 文本 | 触发场景 |
|-----------|-----------|---------|
| `Exception` | `"already connected"` | 重复 `NodeId`（含 zombie entry） |
| `Exception` | `"bus closed"` | Bus 已 shutdown |

**示例：**

```python
engine = await bus.connect(
    NodeInfo("engine/main", "engine", {"session": "s1", "role": "orchestrator"}),
    MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
)
```

!!! note "broadcast_rx 创建时机"
    节点的 broadcast receiver 在 `node_online` 广播**之后**创建。因此节点看不到自己的 `node_online`，但能看到之后连接的其他节点的 `node_online`。

---

#### `Bus.shutdown()`

```python
async def shutdown(self) -> None:
    ...
```

关闭 Bus。关闭 broadcast channel——所有等待中的 `recv()` 以 `Closed` 解除阻塞，所有后续 `send()` 抛 `BusClosed`。

**此方法幂等**——多次调用无额外效果。

**示例：**

```python
await bus.shutdown()

# shutdown 后所有 handle 均不可用
with pytest.raises(Exception):
    await handle.recv()
```

!!! warning "shutdown 后缓冲区仍有残留消息"
    `shutdown()` 调用 `signal_shutdown`，关闭 broadcast channel 的**发送端**——此后不再有新消息进入。但 channel 的**接收端**仍然存活，环形缓冲区中的已发送消息尚未被消费。此时 `recv()` 的行为取决于缓冲区状态：

    | 缓冲区状态 | `recv()` 行为 |
    |-----------|-------------|
    | 有未读消息 | 逐条返回缓冲消息（FIFO 顺序） |
    | 缓冲区为空 | 返回 `Closed` 错误 |

    因此，`shutdown()` 后如果需要验证 `Closed`，应先排空缓冲区：

    ```python
    await bus.shutdown()
    while True:
        try:
            m = handle.try_recv()
            if m is None:
                break
        except Exception:
            break  # channel 已关闭
    with pytest.raises(Exception):
        await handle.recv()  # 此时缓冲区空，返回 Closed
    ```

    **内存不会泄漏：** 环形缓冲区在线节点全部消费完毕（或 receiver 被 drop）后自动释放。
    即使某些消费者已 `disconnect()`，其 `broadcast_rx` 已被 drop，不再持有缓冲区引用，不会阻碍回收。
    最坏情况下（shutdown 后既未 drain 也未 disconnect），内存也会在 `Bus` 对象被 Python GC 时随整个 channel 一起释放——不会有永久残留。

---

#### `Bus.graph()`

```python
def graph(self) -> BusGraph:
    ...
```

**同步方法。** 返回 Bus 当前状态的快照。

**返回：** `BusGraph` 对象，含 `.nodes`（在线 `NodeInfo` 列表）、`.message_count`（已发送的应用消息数）、`.uptime_ms`（运行时间）。

**示例：**

```python
g = bus.graph()
print(f"{len(g.nodes)} 个节点在线, {g.message_count} 条消息, 运行 {g.uptime_ms}ms")
for node in g.nodes:
    print(f"  {node.node_id} ({node.node_type})")
```

---

#### `Bus.message_count`

```python
@property
def message_count(self) -> int:
    ...
```

Bus 创建以来发送的应用消息总数。lifecycle 消息（`node_online`、`node_offline`、`heartbeat_request`）**不计入**。

---

#### `Bus.uptime_ms`

```python
@property
def uptime_ms(self) -> int:
    ...
```

Bus 创建以来经过的毫秒数。

---

### `NodeHandle`

```python
class NodeHandle:
    # 无公共构造函数 — 通过 Bus.connect() 获取
    ...
```

节点收发消息的句柄。由 `Bus.connect()` 创建并返回。

**所有方法在 `disconnect()` 之后均抛 `RuntimeError("already disconnected")`。**

---

#### `NodeHandle.send()`

```python
async def send(
    self,
    msg_type: str,
    to: list[NodeId],
    payload: Any,
) -> SendReceipt:
    ...
```

向 Bus 发送一条消息。`from` 字段自动填充为本节点的 `node_id`。

`send()` **从不阻塞发送方**。Bus 使用 CAN 总线模型的 Lagged 语义：消息进入环形缓冲区后立即返回 `SendReceipt`。如果某个消费者处理过慢导致落后超过一圈，tokio broadcast channel 不会反压发送方，而是让该慢消费者收到 `Lagged(n)`。应用层负责监控慢消费者并做负载均衡或效率优化。

> **Hint for slow consumers:** 如果数据倾斜严重或单节点无法优化到足够快，可以接入一个**快消费者代理**——用 `ToMatch.All` + 最小处理逻辑（写入本地队列或直接 drain），然后转发到慢消费者或分布式消费池并行处理。参见下方 [常见模式](#快消费者代理--fan-out)。

**参数：**

| 参数 | 类型 | 说明 |
|-----------|------|-------------|
| `msg_type` | `str` | 应用定义的消息类型，如 `"action"`、`"job"`、`"tool_call"`。 |
| `to` | `list[NodeId]` | 空列表 `[]` = 广播给所有人。`[NodeId("a"), NodeId("b")]` = 定向发送给指定节点。 |
| `payload` | `Any` | JSON 可序列化的值（`dict`、`list`、`str`、`int`、`float`、`bool`、`None`）。非 JSON 兼容对象抛 `ValueError`。 |

**返回：** `SendReceipt`，含 `.message_id`（UUID）、`.online_nodes`（发送时在线节点总数）、`.matching_nodes`（filter 可能匹配此消息的节点数）。

**异常：**

| 异常类型 | match 文本 | 触发场景 |
|-----------|-----------|---------|
| `Exception` | `"target nodes offline"` | 定向发送时所有目标均离线（广播不会触发此异常） |
| `Exception` | `"bus closed"` | Bus 已 shutdown |
| `RuntimeError` | `"already disconnected"` | Handle 已 disconnect |

**示例：**

```python
# 广播 — 所有 filter 匹配的节点都能收到
receipt = await handle.send("job", [], {"task": "train"})

# 定向 — 仅指定节点能收到（前提是它们的 filter 匹配）
target = NodeId("mcp/fs")
receipt = await handle.send("tool_call", [target], {"tool": "read", "path": "/tmp/x"})

# 查看回执
print(receipt.message_id)      # "550e8400-e29b-41d4-a716-446655440000"
print(receipt.online_nodes)    # 5
print(receipt.matching_nodes)  # 3
```

---

#### `NodeHandle.recv()`

```python
async def recv(self) -> Message:
    ...
```

接收下一条匹配当前节点 filter 的消息。**阻塞**直到有匹配消息到达或 channel 关闭。

**返回：** `Message` — 接收到的消息。`heartbeat_request` 永远不会返回给调用方（内部自动过滤并应答）。

**异常：**

| 异常类型 | match 文本 | 触发场景 |
|-----------|-----------|---------|
| `RuntimeError` | `"already disconnected"` | Handle 已 disconnect |
| `Exception` | `"recv error: Closed"` | Bus 已 shutdown 且缓冲消息已全部 drain |
| `RuntimeError` | `"concurrent recv in progress"` | 另一个 `recv()` 或 `try_recv()` 正在执行 |

**示例：**

```python
msg = await handle.recv()
print(msg.msg_type)        # "job"
print(msg.sender)          # NodeId('engine/main')
print(msg.payload)         # {'task': 'train'}
print(msg.is_broadcast())  # True
```

!!! note "recv() 与 lifecycle 消息"
    `node_online` 和 `node_offline` 是 Bus 自动广播的 lifecycle 消息（`msg_type` 为 `"node_online"` / `"node_offline"`），
    节点**可以选择消费或忽略**，完全由 `MessageFilter` 决定：

    | Filter 配置 | 是否收到 lifecycle |
    |------------|------------------|
    | `types=None`（默认） | ✅ 收到——`"node_online"` 不在过滤白名单中，类型检查通过 |
    | `types=["action", "job"]` | ❌ 收不到——`"node_online"` 不在白名单中，被 filter 过滤 |
    | `to_match=ToMatch.All` | ✅ 收到——且能看到定向给其他节点的消息 |

    - **不关心 lifecycle**：用 `types=["your_type"]` 过滤掉，`recv()` 只返回应用消息。
    - **需要服务发现**：保留默认 filter，消费 `node_online` 来感知新节点上线（获取其 `node_id`、`node_type`、`capabilities`）。
    - **需要完整的上下线日志**：用 `ToMatch.All`，trace 节点可用。

    如果你收到了不需要的 lifecycle 消息，在开始消费应用消息前 drain 即可：
    ```python
    while True:
        msg = await handle.recv()
        if msg.msg_type not in ("node_online", "node_offline"):
            break  # 第一条应用消息
    ```

---

#### `NodeHandle.try_recv()`

```python
def try_recv(self) -> Message | None:
    ...
```

**同步、非阻塞。** 与 `recv()` 逻辑相同，但无可用消息时立即返回 `None`。

**返回：** 有匹配消息返回 `Message`，否则返回 `None`。

**异常：**

| 异常类型 | match 文本 | 触发场景 |
|-----------|-----------|---------|
| `RuntimeError` | `"already disconnected"` | Handle 已 disconnect |
| `RuntimeError` | `"concurrent recv in progress"` | 另一个 `recv()` 正在执行 |
| `Exception` | `"try_recv error"` | 底层 broadcast receiver 异常（罕见） |

**示例：**

```python
# 轮询模式
while True:
    msg = handle.try_recv()
    if msg is not None:
        print(f"收到: {msg.msg_type}")
        break
    await asyncio.sleep(0.01)
```

---

#### `NodeHandle.disconnect()`

```python
async def disconnect(self) -> None:
    ...
```

从 Bus 断开。向所有在线节点广播 `node_offline`，从 nodes map 中立即移除。

**调用此方法后，handle 即被消耗**——后续对该 handle 的所有方法调用均抛 `RuntimeError("already disconnected")`。

**异常：**

| 异常类型 | match 文本 | 触发场景 |
|-----------|-----------|---------|
| `RuntimeError` | `"already disconnected"` | Handle 已 disconnect |

**示例：**

```python
await handle.disconnect()

# 同 NodeId 可立即用新 handle 重连
new_handle = await bus.connect(
    NodeInfo("worker/1", "worker", {}),
    MessageFilter(),
)
```

!!! warning "Disconnect vs Drop"
    如果让 NodeHandle 出作用域而不 `await disconnect()`，节点会变成 **zombie entry**，
    阻塞同 NodeId 重连直到心跳超时清理。**始终显式调用 `await disconnect()`** 做受控下线。

---

#### `NodeHandle.node_info()`

```python
def node_info(self) -> NodeInfo:
    ...
```

**同步方法。** 返回此 handle 创建时使用的 `NodeInfo`。

---

#### `NodeHandle.filter_config()`

```python
def filter_config(self) -> MessageFilter:
    ...
```

**同步方法。** 返回此 handle 创建时使用的 `MessageFilter`。

---

### `NodeId`

```python
class NodeId:
    def __init__(self, id: str) -> None:
        ...
```

节点的唯一标识符。内部包装一个字符串，支持相等比较和哈希。

**方法：**

| 方法 | 签名 | 说明 |
|--------|-----------|-------------|
| `__str__` | `() -> str` | 返回节点 ID 字符串，如 `"engine/main"` |
| `__repr__` | `() -> str` | 返回 `NodeId('engine/main')` |
| `__eq__` | `(other: NodeId) -> bool` | 基于字符串内容比较相等性 |
| `__hash__` | `() -> int` | 基于字符串内容的哈希，可作 `dict` key 和 `set` 成员 |

**示例：**

```python
a = NodeId("engine/main")
b = NodeId("engine/main")
assert a == b
assert hash(a) == hash(b)
assert str(a) == "engine/main"

# 可以用在 dict 和 set 中
registry = {a: "主引擎"}
```

---

### `NodeInfo`

```python
class NodeInfo:
    def __init__(
        self,
        node_id: str,
        node_type: str,
        capabilities: Any,
        online_since: int = 0,
    ) -> None:
        ...
```

节点的身份和能力信息，在 connect 时注册到 Bus。

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|-----------|------|---------|-------------|
| `node_id` | `str` | 必填 | 节点的唯一标识符。命名约定：`"类型/名称"`，如 `"engine/main"`、`"mcp/fs"`。 |
| `node_type` | `str` | 必填 | 节点类别：`"engine"`、`"mcp"`、`"model"`、`"trace"`、`"worker"` 等。 |
| `capabilities` | `Any` | 必填 | JSON 可序列化的任意元数据：工具列表、GPU 信息、session ID 等。 |
| `online_since` | `int` | `0` | Unix 毫秒时间戳。默认 `0` 表示"未指定"。 |

**属性（只读）：**

| 属性 | 类型 | 说明 |
|-----------|------|-------------|
| `.node_id` | `NodeId` | 节点唯一标识符 |
| `.node_type` | `str` | 节点类型类别 |
| `.capabilities` | `Any` | 节点能力（JSON 反序列化后的 Python 对象） |
| `.online_since` | `int` | Unix 毫秒时间戳 |

**示例：**

```python
info = NodeInfo(
    node_id="mcp/fs",
    node_type="mcp",
    capabilities={"tools": ["read", "write", "delete"], "version": "2.1"},
)
print(info.node_id)        # NodeId('mcp/fs')
print(info.capabilities)   # {'tools': ['read', 'write', 'delete'], 'version': '2.1'}
```

---

### `Message`

```python
class Message:
    # 无公共构造函数 — 仅通过 recv() / try_recv() 获取
    ...
```

从 Bus 接收到的消息。由 `NodeHandle.recv()` 和 `NodeHandle.try_recv()` 返回。

**属性（只读）：**

| 属性 | 类型 | 说明 |
|-----------|------|-------------|
| `.id` | `str` | UUID v4 消息唯一标识 |
| `.msg_type` | `str` | 应用消息类型，如 `"job"`、`"action"` |
| `.sender` | `NodeId` | 发送者的节点 ID |
| `.to` | `list[NodeId]` | 目标节点 ID 列表（广播时为空） |
| `.payload` | `Any` | JSON 反序列化后的消息体 |
| `.timestamp` | `int` | Unix 毫秒时间戳 |

**方法：**

| 方法 | 签名 | 说明 |
|--------|-----------|-------------|
| `.is_broadcast()` | `() -> bool` | `to` 为空返回 `True` |
| `.is_for(node_id)` | `(node_id: NodeId) -> bool` | `node_id` 在 `to` 列表中返回 `True` |

**示例：**

```python
msg = await handle.recv()
print(msg.id)              # "550e8400-e29b-41d4-a716-446655440000"
print(msg.msg_type)        # "tool_call"
print(str(msg.sender))     # "engine/main"
print(msg.is_broadcast())  # False
print(msg.is_for(NodeId("mcp/fs")))  # True
print(msg.payload)         # {'tool': 'read', 'path': '/tmp/x'}
```

---

### `MessageFilter`

```python
class MessageFilter:
    def __init__(
        self,
        types: list[str] | None = None,
        to_match: ToMatch | None = None,
    ) -> None:
        ...
```

控制 `NodeHandle` 接收哪些消息。**每个节点独立配置，接收侧生效。**

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|-----------|------|---------|-------------|
| `types` | `list[str] \| None` | `None` | 消息类型白名单。`None` = 接受所有类型。`["action", "job"]` = 只接收这两种。 |
| `to_match` | `ToMatch \| None` | `BroadcastAndDirectedToMe` | 目标匹配策略。`None` 默认 `BroadcastAndDirectedToMe`。 |

**属性（只读）：**

| 属性 | 类型 | 说明 |
|-----------|------|-------------|
| `.types` | `list[str] \| None` | 类型白名单 |
| `.to_match` | `ToMatch` | 目标匹配策略 |

**示例：**

```python
# 处理 "job" 和 "infer" 的 worker 节点
f = MessageFilter(types=["job", "infer"])

# Trace 节点 — 全量消费
f = MessageFilter(types=None, to_match=ToMatch.All)

# 只响应定向调用、不接收广播的服务节点
f = MessageFilter(types=None, to_match=ToMatch.DirectedToMe)
```

---

### `ToMatch`

```python
class ToMatch:
    All: ToMatch                       # 接收所有消息（广播 + 定向给任何人）
    BroadcastOnly: ToMatch             # 仅接收广播消息（to=[]）
    DirectedToMe: ToMatch              # 仅接收定向给自己的消息
    BroadcastAndDirectedToMe: ToMatch  # 默认：广播 + 定向给自己的都接收
```

目标匹配策略。四个单例实例，通过类属性访问。

!!! note "ToMatch 不是 Enum"
    `ToMatch` 值是单例实例，不是 Python `Enum` 成员。用 `==` 比较：
    ```python
    assert ToMatch.All != ToMatch.BroadcastOnly
    assert f.to_match == ToMatch.BroadcastAndDirectedToMe
    ```

**示例：**

```python
from arf import ToMatch

ToMatch.All                       # 全量消费
ToMatch.BroadcastOnly             # 只关心广播
ToMatch.DirectedToMe              # 只响应定向调用
ToMatch.BroadcastAndDirectedToMe  # 默认：广播 + 调用我的
```

---

### `SendReceipt`

```python
class SendReceipt:
    # 无公共构造函数 — 由 NodeHandle.send() 返回
    ...
```

`NodeHandle.send()` 返回的发送确认。仅确认消息已进入广播通道，不确认某个特定节点已收到。

**属性（只读）：**

| 属性 | 类型 | 说明 |
|-----------|------|-------------|
| `.message_id` | `str` | 已发送消息的 UUID v4 |
| `.online_nodes` | `int` | 发送时在线节点总数（含自己） |
| `.matching_nodes` | `int` | filter 可能匹配此消息的在线节点数 |

!!! note "matching_nodes 是下限估计"
    `matching_nodes` 统计 `MessageFilter.types` 包含此消息 `msg_type`（或为 `None`）的节点。它**不考虑** `to_match` 策略——即使某节点设置了 `DirectedToMe`，广播消息也会将其计入 `matching_nodes`。

---

### `BusGraph`

```python
class BusGraph:
    # 无公共构造函数 — 由 Bus.graph() 返回
    ...
```

Bus 健康状态的即时快照。

**属性（只读）：**

| 属性 | 类型 | 说明 |
|-----------|------|-------------|
| `.nodes` | `list[NodeInfo]` | 当前在线节点 |
| `.message_count` | `int` | Bus 创建以来的应用消息数 |
| `.uptime_ms` | `int` | Bus 创建以来的毫秒数 |

**示例：**

```python
g = bus.graph()
assert len(g.nodes) >= 0
assert g.message_count >= 0
assert g.uptime_ms >= 0
```

---

## 常见模式

### Worker Pool（应用层负载均衡）

多个同类型 worker 全收每一条广播，应用层决定由哪个 worker 处理（如一致性哈希、轮询）。

```python
async def worker_pool():
    bus = Bus(heartbeat_interval_ms=5000, heartbeat_timeout_ms=15000, channel_capacity=128)

    # 调度器发送任务
    dispatcher = await bus.connect(
        NodeInfo("engine/dispatcher", "engine", {}),
        MessageFilter(),
    )

    # 4 个 GPU worker，filter 完全相同
    workers = []
    for i in range(4):
        w = await bus.connect(
            NodeInfo(f"model/gpu-{i}", "model", {"gpu": i}),
            MessageFilter(types=["infer"]),
        )
        workers.append(w)

    # 广播一条推理任务 — 4 个 worker 全部收到
    await dispatcher.send("infer", [], {"prompt": "hello"})

    for w in workers:
        msg = await w.recv()
        assert msg.payload == {"prompt": "hello"}

    await bus.shutdown()
```

!!! note "Bus 不做负载均衡"
    Bus 向所有匹配节点广播。如果每条任务只需要一个 worker 处理，在应用层实现选择逻辑（轮询、一致性哈希、或通过 `DirectedToMe` 实现会话粘性）。

### 会话粘性

通过定向发送将同一 session 的消息始终路由到同一个 worker。

```python
async def session_affinity():
    bus = Bus()

    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(),
    )
    worker_s1 = await bus.connect(
        NodeInfo("worker/session-1", "worker", {"session": "s1"}),
        MessageFilter(to_match=ToMatch.DirectedToMe),
    )

    # 专门发送给 session-1 的 worker
    target = NodeId("worker/session-1")
    await engine.send("tool_call", [target], {"tool": "read", "path": "/data/s1"})

    msg = await worker_s1.recv()
    print(msg.payload)  # {'tool': 'read', 'path': '/data/s1'}

    await bus.shutdown()
```

### 可观测性 / Tracing

设置一个 `ToMatch.All` 的 trace 节点，监控 Bus 上所有消息。

```python
async def tracing():
    bus = Bus(heartbeat_interval_ms=5000, heartbeat_timeout_ms=15000, channel_capacity=1024)

    # Trace 节点 — 全量消费
    trace = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )

    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {}),
        MessageFilter(),
    )
    worker = await bus.connect(
        NodeInfo("mcp/fs", "mcp", {}),
        MessageFilter(types=["tool_call"]),
    )

    # Engine 广播任务
    await engine.send("job", [], {"task": "compress"})

    # Trace 看到了（ToMatch.All）
    trace_msg = await trace.recv()
    print(f"[trace] {trace_msg.msg_type} from {trace_msg.sender}")
    # → [trace] job from engine/main

    # Engine 发送定向 tool_call
    await engine.send("tool_call", [NodeId("mcp/fs")], {"tool": "read"})

    # Trace 和 worker 都收到定向消息
    # （trace 有 ToMatch.All，worker 的 filter 匹配 "tool_call"）
    worker_msg = await worker.recv()
    trace_msg2 = await trace.recv()
    print(f"[worker] 收到 {worker_msg.msg_type}")
    print(f"[trace]  看到 {trace_msg2.msg_type} (定向给 {trace_msg2.to})")

    await bus.shutdown()
```

### 快消费者代理 / Fan-out

当某个消费者处理逻辑过重导致频繁 `Lagged(n)`，且无法对单节点做效率优化时（例如需要调用慢速外部 API、做大规模计算、或数据本身倾斜），可以接入一个**快消费者代理**——它用最小开销捕获消息，立即 drain，然后转发到慢消费者或分布式消费池。

```python
async def fast_consumer_fanout():
    bus = Bus(heartbeat_interval_ms=5000, heartbeat_timeout_ms=15000, channel_capacity=256)

    # 生产者
    producer = await bus.connect(
        NodeInfo("engine/producer", "engine", {}),
        MessageFilter(),
    )

    # 快消费者代理：ToMatch.All，每条消息只做 enqueue，永不 Lagged
    queue: asyncio.Queue = asyncio.Queue()
    async def fast_drain(handle):
        """快速从 Bus 拉取消息放入本地队列，永远不阻塞 Bus 环形缓冲区。"""
        while True:
            try:
                msg = await handle.recv()          # 快：只做网络层拉取
                await queue.put(msg)                # 快：入队即返回
            except Exception:
                break  # Bus 关闭

    fast_agent = await bus.connect(
        NodeInfo("agent/fast-drain", "agent", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )
    asyncio.ensure_future(fast_drain(fast_agent))

    # 慢消费者池：从本地队列取消息，可以并行处理
    async def slow_worker(worker_id: int):
        while True:
            msg = await queue.get()
            # 这里可以是耗时的外部 API 调用、GPU 推理、数据库写入等
            print(f"[worker-{worker_id}] 处理 type={msg.msg_type} payload={msg.payload}")
            await asyncio.sleep(0.1)  # 模拟慢处理
            queue.task_done()

    # 启动 4 个慢消费者并行处理
    workers = [asyncio.ensure_future(slow_worker(i)) for i in range(4)]

    # 生产者高速发送
    for i in range(100):
        await producer.send("job", [], {"seq": i})

    await asyncio.sleep(0.5)  # 等待消费
    await bus.shutdown()
```

核心思路：**Bus 环形缓冲区只负责高速分发，不做背压**。快代理把消息卸到应用层队列后，缓冲区的 slot 立即释放。慢处理逻辑完全与 Bus 解耦，可以自由伸缩 worker 数量、使用进程池、甚至转发到外部消息队列。

### 优雅关闭

```python
async def graceful_shutdown():
    bus = Bus()
    h1 = await bus.connect(NodeInfo("node-1", "test", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("node-2", "test", {}), MessageFilter())

    # 发送一些消息
    await h1.send("msg", [], {"n": 1})
    await h1.send("msg", [], {"n": 2})

    # 1. 先断开所有节点
    await h1.disconnect()
    await h2.disconnect()

    # 2. 再关闭 Bus
    await bus.shutdown()
```

或者在节点仍在线的场景下 shutdown：

```python
async def shutdown_with_nodes_online():
    bus = Bus()
    h = await bus.connect(NodeInfo("node-1", "test", {}), MessageFilter())

    await bus.shutdown()

    # 先 drain 缓冲区中的残留消息
    while True:
        try:
            m = h.try_recv()
            if m is None:
                break
        except Exception:
            break

    # 此时 recv 应该抛异常
    with pytest.raises(Exception):
        await h.recv()
```

### 崩溃后重连

```python
async def reconnect_after_crash():
    bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=300, channel_capacity=32)

    # 主 worker 连接后崩溃（未调用 disconnect）
    async def crash():
        w = await bus.connect(
            NodeInfo("worker/main", "worker", {}),
            MessageFilter(),
        )
        # 模拟崩溃 — 不调用 disconnect()

    await crash()

    # 立即重连会失败 — zombie entry 仍然存在
    with pytest.raises(Exception, match="already connected"):
        await bus.connect(
            NodeInfo("worker/main", "worker", {}),
            MessageFilter(),
        )

    # 等待心跳超时清理 zombie（timeout_ms=300ms）
    await asyncio.sleep(0.5)

    # 现在可以重连了
    w2 = await bus.connect(
        NodeInfo("worker/main", "worker", {}),
        MessageFilter(),
    )
    print("zombie 清理后重连成功")

    await w2.disconnect()
    await bus.shutdown()
```

### 服务发现与初始化时序

`node_online` 广播可以用来做服务发现——节点上线后感知已在线的 peer，获取其 capabilities。但常见需求是：**B 需要在 A 发消息之前完成初始化**。这需要控制连接顺序 + 就绪信号。

```python
async def service_discovery_and_ready_signal():
    bus = Bus()

    # 1. B 先 connect —— 保证 A 上线时 B 已经在监听
    b = await bus.connect(
        NodeInfo("worker/b", "worker", {"ready": False}),
        MessageFilter(),
    )

    # 2. A connect —— 此时 B 已经在线，B 的 recv() 会收到 A 的 node_online
    a = await bus.connect(
        NodeInfo("engine/a", "engine", {"role": "producer"}),
        MessageFilter(),
    )

    # 3. B 通过 node_online 感知 A 已上线，完成初始化
    while True:
        msg = await b.recv()
        if msg.msg_type == "node_online" and str(msg.sender) == "engine/a":
            caps = msg.payload["node_info"]["capabilities"]
            print(f"B 发现 A 上线: role={caps['role']}")
            break

    # 4. B 初始化完毕，发送就绪信号给 A
    a_id = NodeId("engine/a")
    await b.send("ready", [a_id], {"worker": "worker/b", "status": "ready"})

    # 5. A 收到就绪信号后才开始发送应用消息
    ready_msg = await a.recv()
    assert ready_msg.msg_type == "ready"
    await a.send("job", [], {"task": "process", "data": 42})

    # 6. B 消费应用消息（此时已经初始化完毕，不会再收到 node_online）
    while True:
        msg = await b.recv()
        if msg.msg_type == "job":
            print(f"B 处理: {msg.payload}")
            break

    await a.disconnect()
    await b.disconnect()
    await bus.shutdown()
```

**关键时序：**
1. 消费者先 connect → 生产者后 connect → 消费者一定能收到生产者的 `node_online`（rx 创建时机保证）
2. 消费者初始化完毕 → 定向 `ready` 信号 → 生产者收到后才开始业务发送
3. 如果不需要感知上线，直接用 `types=["job"]` 过滤掉 `node_online`，完全忽略 lifecycle

---

## 异常速查表

| 异常类型 | match 文本 | 触发原因 |
|---------------|-----------|---------------|
| `Exception` | `"already connected"` | 重复 `NodeId` 或 zombie entry 尚未清理 |
| `Exception` | `"bus closed"` | `shutdown()` 后调用 `connect()` 或 `send()` |
| `Exception` | `"target nodes offline"` | 定向发送时所有目标均离线 |
| `Exception` | `"recv error: Closed"` | `shutdown()` 后缓冲区为空时调用 `recv()` |
| `Exception` | `"try_recv error"` | `try_recv()` 内部异常（罕见） |
| `RuntimeError` | `"already disconnected"` | 对已 disconnect 的 `NodeHandle` 调用任何方法 |
| `RuntimeError` | `"concurrent recv in progress"` | 另一个 `recv()` 活跃时调用 `recv()` 或 `try_recv()` |
| `RuntimeError` | `"concurrent access in progress"` | 并发调用 `node_info()` / `filter_config()` |
| `ValueError` | — | `payload` 或 `capabilities` 不是 JSON 可序列化对象 |

---

## Python 与 Rust API 差异

| 维度 | Rust (`arf-bus`) | Python (`arf.Bus`) |
|--------|-----------------|-------------------|
| `Bus::shutdown` | `async fn shutdown(self)` — 消费 Bus | `async fn shutdown(&self)` — 调用 `signal_shutdown`，关闭 broadcast channel |
| `send()` 错误类型 | `Result<SendReceipt, SendError>` 枚举 | Python `Exception`，消息文本为 Display 输出 |
| `connect()` 错误类型 | `Result<NodeHandle, ConnectError>` 枚举 | Python `Exception`，消息文本为 Display 输出 |
| `NodeHandle` disconnect 后 | 编译期检查（被 `disconnect` 消费） | 运行时 `RuntimeError` |
| Payload 类型 | `serde_json::Value` | `Any`（通过 `json.dumps` / `json.loads` 桥接） |
| 异步返回类型 | `impl Future<Output = T>` | Python `Future`（非 coroutine — 用 `ensure_future()` 而非 `create_task()`） |
| 弱引用 | 不支持 | 不支持（`TypeError: cannot create weak reference`） |

---

## 参考

- [Phase 1 Bus 架构设计](../v1.x/phase1-bus-design.md) — Rust 侧架构和设计决策
- [Task 1.11 — Python 测试记录](../v1.x/task-1.11-python-tests.md) — 66 测试套件及其行为发现
