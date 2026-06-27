# 任务 1.12：Python API 文档与教学示例

> Phase 1 — Bus 消息总线第十二项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.11 Python 测试全部通过 (66 tests, 0 failures)

---

## 概述

本文档是 Phase 1 Bus 的 **Python API 完整参考**。所有类型通过 PyO3 从 Rust 导出，对 Python 用户来说是原生 Python 对象。

---

## 安装与导入

```bash
cd py-arf && ../.venv/bin/python -m maturin develop
```

```python
from arf import (
    Bus, BusGraph, Message, MessageFilter,
    NodeHandle, NodeId, NodeInfo, SendReceipt, ToMatch,
    __version__,
)

print(__version__)  # "1.0.0-alpha.0"
```

`__version__` 是直接暴露的模块级常量，不是函数调用。

---

## 类型参考

### NodeId

节点唯一标识。内部是字符串，支持 `==`/`hash`/`str`/`repr`。

```python
nid = NodeId("engine/main")
str(nid)        # "engine/main"
repr(nid)       # "NodeId('engine/main')"
nid == NodeId("engine/main")  # True
hash(nid)       # hashable, usable as dict key / set member
```

| 成员 | 类型 | 说明 |
|------|------|------|
| `NodeId(id: str)` | 构造 | 创建 NodeId |
| `__str__()` | `-> str` | 返回内部字符串 |
| `__repr__()` | `-> str` | `NodeId('...')` |
| `__eq__(other)` | `-> bool` | 字符串相等 |
| `__hash__()` | `-> int` | 允许 set/dict 使用 |

---

### NodeInfo

节点上线时向 Bus 注册的身份信息。

```python
info = NodeInfo(
    node_id="engine/main",              # str — 唯一标识
    node_type="engine",                 # str — 类型标签 (engine/mcp/model/trace/worker/...)
    capabilities={"session": "s1"},     # Any — JSON 可序列化的任意数据
    online_since=0,                     # int — Unix 毫秒时间戳，默认 0
)
```

| 成员 | 类型 | 说明 |
|------|------|------|
| `NodeInfo(node_id, node_type, capabilities, online_since=0)` | 构造 | capabilities 通过 `json.dumps` 序列化，非 JSON 兼容类型抛 `ValueError` |
| `.node_id` | `NodeId` | 只读 |
| `.node_type` | `str` | 只读 |
| `.capabilities` | `Any` | 只读，JSON 反序列化后的 Python 对象 |
| `.online_since` | `int` | 只读 |

---

### MessageFilter

控制节点接收哪些消息。**接收侧过滤**——消息总是广播到所有节点，由各节点自行过滤。

```python
# 默认：接收所有类型的广播 + 定向到自己的消息
f = MessageFilter()

# 只收 "action" 和 "event" 类型
f = MessageFilter(types=["action", "event"])

# 只收定向到自己的消息（不收广播）
f = MessageFilter(to_match=ToMatch.DirectedToMe)

# 全量消费（trace 节点用）
f = MessageFilter(types=None, to_match=ToMatch.All)

# 只收广播
f = MessageFilter(to_match=ToMatch.BroadcastOnly)
```

| 成员 | 类型 | 说明 |
|------|------|------|
| `MessageFilter(types=None, to_match=None)` | 构造 | `types=None` 表示接受所有消息类型。`to_match=None` 默认 `BroadcastAndDirectedToMe` |
| `.types` | `Optional[list[str]]` | 只读，白名单；`None` = 全部通过 |
| `.to_match` | `ToMatch` | 只读，目标匹配策略 |

---

### ToMatch

消息目标匹配策略的枚举（通过类属性暴露四个单例）。

```python
ToMatch.All                       # 收所有消息（广播 + 定向，无论 to 是谁）
ToMatch.BroadcastOnly             # 只收广播 (to=[])
ToMatch.DirectedToMe              # 只收定向到自己的
ToMatch.BroadcastAndDirectedToMe  # 默认：广播 + 定向到自己的都收
```

| 属性 | 说明 |
|------|------|
| `ToMatch.All` | trace 节点全量消费 |
| `ToMatch.BroadcastOnly` | 只关心广播 |
| `ToMatch.DirectedToMe` | 只收定向消息 |
| `ToMatch.BroadcastAndDirectedToMe` | **默认**，最常见配置 |

四个值是单例，`==` 可比较：`ToMatch.All != ToMatch.BroadcastOnly`。

---

### SendReceipt

`NodeHandle.send()` 的返回值。确认消息已进入广播通道。

```python
receipt = await handle.send("action", [], {"cmd": "run"})
print(receipt.message_id)     # "550e8400-e29b-..."
print(receipt.online_nodes)   # 3  (发送时在线节点数，含自身)
print(receipt.matching_nodes) # 2  (filter 可能匹配的在线节点数)
```

| 属性 | 类型 | 说明 |
|------|------|------|
| `.message_id` | `str` | UUID v4 |
| `.online_nodes` | `int` | 发送时刻在线节点总数（含自身） |
| `.matching_nodes` | `int` | filter 类型匹配的在线节点数 |

> `message_count` (Bus 级别) 只统计**应用消息**。`node_online`/`node_offline`/`heartbeat_request` 不计入。

---

### Message

`NodeHandle.recv()` / `try_recv()` 的返回值。**没有公共构造函数**——只能通过接收获得。

```python
msg = await handle.recv()
print(msg.id)              # "550e8400-e29b-..."
print(msg.msg_type)        # "action"
print(msg.sender)          # NodeId('engine/main')
print(msg.to)              # [] (broadcast) 或 [NodeId('mcp/fs')]
print(msg.payload)         # {"cmd": "run"}  (dict/list/int/str/float/None)
print(msg.timestamp)       # 1719000000000 (Unix ms)
print(msg.is_broadcast())  # True
print(msg.is_for(target))  # True/False
```

| 成员 | 类型 | 说明 |
|------|------|------|
| `.id` | `str` | 只读，UUID v4 |
| `.msg_type` | `str` | 只读，消息类型标签 |
| `.sender` | `NodeId` | 只读，发送者 |
| `.to` | `list[NodeId]` | 只读，目标列表（广播时为空） |
| `.payload` | `Any` | 只读，JSON 反序列化后的 Python 对象 |
| `.timestamp` | `int` | 只读，Unix 毫秒 |
| `.is_broadcast()` | `-> bool` | `to` 为空 |
| `.is_for(node_id)` | `-> bool` | `node_id` 在 `to` 列表中 |

---

### BusGraph

`Bus.graph()` 的返回值。Bus 健康状态快照。

```python
g = bus.graph()
for n in g.nodes:
    print(f"{n.node_id} ({n.node_type})")
print(f"messages: {g.message_count}, uptime: {g.uptime_ms}ms")
```

| 属性 | 类型 | 说明 |
|------|------|------|
| `.nodes` | `list[NodeInfo]` | 当前在线节点快照 |
| `.message_count` | `int` | 应用消息总数（不含 lifecycle） |
| `.uptime_ms` | `int` | Bus 启动以来的毫秒数 |

---

### Bus

消息总线的核心。内部维护一个 tokio broadcast channel 和一个消息循环。

```python
bus = Bus(
    heartbeat_interval_ms=1000,   # 心跳间隔 (ms)，默认 1000
    heartbeat_timeout_ms=3000,    # 心跳超时 (ms)，默认 3000
    channel_capacity=16,          # 环形缓冲区容量，默认 16
)
```

**生命周期**：`Bus()` → `connect()` → `send()`/`recv()` → `disconnect()` → `shutdown()`

| 成员 | 异步 | 说明 |
|------|------|------|
| `Bus(heartbeat_interval_ms=1000, heartbeat_timeout_ms=3000, channel_capacity=16)` | — | 构造。内部启动 tokio 消息循环和心跳定时器 |
| `.message_count` | — | 属性，应用消息总数 |
| `.uptime_ms` | — | 属性，Bus 运行毫秒数 |
| `.graph()` | — | 同步，返回 `BusGraph` 快照 |
| `await .connect(info, filter)` | **是** | 注册节点，返回 `NodeHandle`。重复 NodeId 抛异常 (`"already connected"`)。shutdown 后抛异常 (`"bus closed"`) |
| `await .shutdown()` | **是** | 关闭 Bus。关闭 broadcast channel，所有 receiver 收到 Closed，所有 send 返回错误 |

**错误**：

| 场景 | 异常 | match 文本 |
|------|------|-----------|
| 重复 NodeId | `Exception` | `"already connected"` |
| shutdown 后 connect | `Exception` | `"bus closed"` |

---

### NodeHandle

节点的操作句柄。通过 `Bus.connect()` 获得。

```python
handle = await bus.connect(info, filter)

# 发送
receipt = await handle.send("action", [], {"cmd": "run"})
receipt = await handle.send("action", [target_id], {"cmd": "run"})

# 接收
msg = await handle.recv()     # 阻塞等待
msg = handle.try_recv()       # 不阻塞，无消息返回 None

# 查询
info = handle.node_info()     # NodeInfo
f = handle.filter_config()    # MessageFilter

# 断开
await handle.disconnect()
```

#### send()

```python
await handle.send(msg_type: str, to: list[NodeId], payload: Any) -> SendReceipt
```

- `payload` 可以是任何 JSON 可序列化的 Python 对象（dict/list/str/int/float/bool/None）
- `to=[]` 表示广播（所有节点可见，受各自 filter 约束）
- `to=[NodeId("mcp/fs")]` 表示定向发送
- `from` 字段由 Bus 自动填充为当前节点的 `node_id`

**错误**：

| 场景 | 异常 | match 文本 |
|------|------|-----------|
| 全部目标离线 | `Exception` | `"target nodes offline"` |
| Bus 已关闭 | `Exception` | `"bus closed"` |
| 缓冲区满 | `Exception` | `"bus buffer full"` |
| 已 disconnect | `RuntimeError` | `"already disconnected"` |

#### recv()

```python
msg = await handle.recv()  # -> Message
```

- **阻塞**：没有匹配消息时等待（内部 `tokio broadcast Receiver`）
- **自动过滤**：`heartbeat_request` 不会返回给调用方，NodeHandle 内部自动应答
- **Filter 匹配**：只返回通过 `MessageFilter` 的消息

**错误**：

| 场景 | 异常 | match 文本 |
|------|------|-----------|
| 已 disconnect | `RuntimeError` | `"already disconnected"` |
| shutdown 后 (buffer drain 完毕) | `Exception` | `"recv error"` / `"Closed"` |
| 有并发的 recv | `RuntimeError` | `"concurrent recv in progress"` |

#### try_recv()

```python
msg = handle.try_recv()  # -> Optional[Message]
```

- **非阻塞**：无消息立即返回 `None`
- 其他行为和 filter 规则同 `recv()`

**错误**：

| 场景 | 异常 | match 文本 |
|------|------|-----------|
| 已 disconnect | `RuntimeError` | `"already disconnected"` |
| 有并发的 recv | `RuntimeError` | `"concurrent recv in progress"` |

#### disconnect()

```python
await handle.disconnect()
```

- 广播 `node_offline` 给所有在线节点
- 从 Bus nodes map 中立即移除
- **消耗句柄**：disconnect 后该 handle 的所有方法都抛 `RuntimeError`
- 同 NodeId 可立即重连（新 handle）
- 重复 disconnect 抛 `RuntimeError` (`"already disconnected"`)

#### node_info() / filter_config()

```python
info = handle.node_info()        # -> NodeInfo
f = handle.filter_config()       # -> MessageFilter
```

- 同步方法，立即返回
- disconnect 后抛 `RuntimeError`

---

## 异常速查表

| 异常类型 | match 文本 | 触发场景 |
|---------|-----------|---------|
| `Exception` | `"already connected"` | 重复 NodeId connect |
| `Exception` | `"bus closed"` | shutdown 后 connect / send |
| `Exception` | `"target nodes offline"` | 定向发送全部目标离线 |
| `Exception` | `"bus buffer full"` | broadcast channel 缓冲区满 |
| `Exception` | `"recv error: Closed"` | shutdown 后 recv (buffer 空) |
| `Exception` | `"try_recv error"` | try_recv 底层异常 |
| `RuntimeError` | `"already disconnected"` | disconnect 后调用任何方法 |
| `RuntimeError` | `"concurrent recv in progress"` | recv 期间 try_recv |
| `RuntimeError` | `"concurrent access in progress"` | node_info/filter_config 并发冲突 |
| `ValueError` | — | payload JSON 序列化失败 |

---

## 设计行为要点

以下行为由测试确认，非直觉但属于设计意图：

### 1. `message_count` 只计应用消息

`node_online`、`node_offline`、`heartbeat_request` 不计入 `Bus.message_count` 和 `BusGraph.message_count`。只有通过 `NodeHandle.send()` 发送的应用消息才计数。

### 2. `broadcast_rx` 创建时机

节点的 broadcast Receiver 在 `node_online` 广播**之后**创建。因此：
- 节点看不到自己的 `node_online`
- 节点能看到后续连接的节点的 `node_online`
- 测试中需要在收应用消息前 drain 掉 node_online 残留

### 3. shutdown 后 ring buffer 仍有残留

`signal_shutdown` 关闭 broadcast channel 后，ring buffer 中可能还有未消费的消息。`recv()` 会先返回这些缓冲消息，消耗完后才返回 `Closed`。测试中 shutdown 后应先 drain 再验证 Closed。

### 4. PyO3 async 方法返回 `Future` 非 coroutine

PyO3 的 `future_into_py` 返回 Python `Future` 对象，不是 coroutine。不能用 `asyncio.create_task()` 包装，需用 `asyncio.ensure_future()`。

```python
# 错误
task = asyncio.create_task(handle.recv())  # TypeError!

# 正确
task = asyncio.ensure_future(handle.recv())

# 更简单：直接 await
msg = await handle.recv()
```

### 5. Handle drop 不调用 disconnect → zombie entry

如果 Python 用户让 NodeHandle 出作用域而不显式 `await disconnect()`，NodeEntry 残留在 Bus 的 nodes map 中（zombie entry）。同 NodeId 重连被拒绝，直到心跳超时清理该条目。这是"崩溃检测"的标准模式，不是内存泄漏。

### 6. 广播语义（非队列消费）

一条广播消息被所有匹配 filter 的节点收到。A recv 消费一条广播后，B 仍能 recv 到同一条消息。Bus 不做排他消费或负载均衡——应用层自行决定哪个 worker 处理。

### 7. 定向到全部离线目标抛异常

`send("msg", [offline_node], payload)` 在所有目标均离线时抛 `Exception("target nodes offline")`。部分目标在线时仍成功，只投递给在线目标。

---

## 教学示例：`phase1_bus_hello.py`

完整演示 Bus API 生命周期：创建→连接→广播→定向发送→过滤→图查询→断连→重连→关闭。

```bash
cd py-arf && ../.venv/bin/python python/arf/examples/phase1_bus_hello.py
```

```python
"""
Phase 1 Bus teaching example: multi-node chat room on a shared message bus.

Demonstrates the full Bus API lifecycle:
  - Bus creation with custom heartbeat/channel parameters
  - Node connection with different types and filters
  - Broadcast and directed messaging
  - Message receipt verification (id, sender, payload, is_broadcast, is_for)
  - Filter behavior (types whitelist, ToMatch modes)
  - Bus health graph inspection
  - Node disconnect and reconnect
  - Graceful shutdown

Run:
    cd py-arf && ../.venv/bin/python python/arf/examples/phase1_bus_hello.py
"""
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch


async def main():
    # ── Create Bus ───────────────────────────────────────────────────
    bus = Bus(
        heartbeat_interval_ms=5000,   # 5s heartbeat tick
        heartbeat_timeout_ms=15000,   # 15s timeout → node considered offline
        channel_capacity=64,          # ring buffer for 64 messages
    )
    print("[Bus] created")

    # ── Connect nodes ────────────────────────────────────────────────
    # engine/main: orchestrator — sends and receives everything
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {"session": "demo", "role": "orchestrator"}),
        MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    print("[engine/main] connected")

    # mcp/fs: filesystem tool — only listens for "tool_call" messages
    fs_worker = await bus.connect(
        NodeInfo("mcp/fs", "mcp", {"tools": ["read", "write"]}),
        MessageFilter(types=["tool_call"], to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    print("[mcp/fs] connected")

    # trace/obs: observer — sees everything (ToMatch.All)
    trace = await bus.connect(
        NodeInfo("trace/obs", "trace", {}),
        MessageFilter(types=None, to_match=ToMatch.All),
    )
    print("[trace/obs] connected (ToMatch.All — sees everything)")

    # ── Inspect graph ────────────────────────────────────────────────
    g = bus.graph()
    print(f"\n[graph] {len(g.nodes)} nodes online, {g.message_count} messages, uptime {g.uptime_ms}ms")
    for n in g.nodes:
        print(f"  {n.node_id} ({n.node_type}) caps={n.capabilities}")

    # ── Broadcast message ────────────────────────────────────────────
    receipt = await engine.send("job", [], {"task": "compress", "file": "data.txt"})
    print(f"\n[engine] broadcast 'job' → receipt online={receipt.online_nodes} matching={receipt.matching_nodes}")

    # trace sees everything via ToMatch.All
    trace_msg = await trace.recv()
    print(f"[trace] recv: type={trace_msg.msg_type} sender={trace_msg.sender} "
          f"is_broadcast={trace_msg.is_broadcast()} payload={trace_msg.payload}")

    # engine also receives its own broadcast (broadcast rx includes self)
    # May need to drain lifecycle messages first
    engine_msg = await engine.recv()
    print(f"[engine] recv: type={engine_msg.msg_type} sender={engine_msg.sender}")

    # ── Directed message ─────────────────────────────────────────────
    target = NodeId("mcp/fs")
    receipt2 = await engine.send(
        "tool_call", [target],
        {"tool": "read", "path": "/tmp/data.txt"},
    )
    print(f"\n[engine] directed 'tool_call' to mcp/fs → receipt online={receipt2.online_nodes} matching={receipt2.matching_nodes}")

    # Only mcp/fs receives this directed message
    fs_msg = await fs_worker.recv()
    # fs_worker may need to drain node_online first
    if fs_msg.msg_type != "tool_call":
        print(f"[mcp/fs] drain lifecycle: type={fs_msg.msg_type}")
        fs_msg = await fs_worker.recv()
    print(f"[mcp/fs] recv: type={fs_msg.msg_type} sender={fs_msg.sender} "
          f"is_for(fs)={fs_msg.is_for(target)} payload={fs_msg.payload}")

    # ── Try receive (non-blocking) ───────────────────────────────────
    nothing = fs_worker.try_recv()
    print(f"\n[mcp/fs] try_recv → {nothing}  (no pending messages)")

    # ── Disconnect & reconnect ───────────────────────────────────────
    await fs_worker.disconnect()
    print(f"\n[mcp/fs] disconnected — graph now has {len(bus.graph().nodes)} nodes")

    # Reconnect with same NodeId
    fs_v2 = await bus.connect(
        NodeInfo("mcp/fs", "mcp", {"tools": ["read", "write", "delete"]}),
        MessageFilter(types=["tool_call"], to_match=ToMatch.BroadcastAndDirectedToMe),
    )
    print(f"[mcp/fs] reconnected with upgraded capabilities")

    # ── Bus stats ────────────────────────────────────────────────────
    g2 = bus.graph()
    print(f"\n[graph] {len(g2.nodes)} nodes, {bus.message_count} messages, uptime {bus.uptime_ms}ms")

    # ── Shutdown ─────────────────────────────────────────────────────
    await bus.shutdown()
    print(f"\n[Bus] shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
```

**预期输出**：

```
[Bus] created
[engine/main] connected
[mcp/fs] connected
[trace/obs] connected (ToMatch.All — sees everything)

[graph] 3 nodes online, 0 messages, uptime 0ms
  trace/obs (trace) caps={}
  engine/main (engine) caps={'role': 'orchestrator', 'session': 'demo'}
  mcp/fs (mcp) caps={'tools': ['read', 'write']}

[engine] broadcast 'job' → receipt online=3 matching=2
[trace] recv: type=job sender=engine/main is_broadcast=True payload={'file': 'data.txt', 'task': 'compress'}
[engine] recv: type=node_online sender=mcp/fs

[engine] directed 'tool_call' to mcp/fs → receipt online=3 matching=1
[mcp/fs] recv: type=tool_call sender=engine/main is_for(fs)=True payload={'path': '/tmp/data.txt', 'tool': 'read'}

[mcp/fs] try_recv → None  (no pending messages)

[mcp/fs] disconnected — graph now has 2 nodes
[mcp/fs] reconnected with upgraded capabilities

[graph] 3 nodes, 2 messages, uptime 1ms

[Bus] shutdown complete
```

---

## 与 Rust API 的差异

| 项目 | Rust | Python |
|------|------|--------|
| shutdown | `async fn shutdown(self)` 消费 Bus | `async fn shutdown(&self)` → 调用 `signal_shutdown`，关闭 broadcast channel |
| send 的 to 参数 | `Vec<NodeId>` (Into<Vec>) | `list[NodeId]` |
| payload 类型 | `serde_json::Value` | `Any`（通过 `json.dumps`/`json.loads` 桥接） |
| async 方法 | 返回 `impl Future` | 返回 Python `Future`（非 coroutine） |
| 错误类型 | Rust enum (`SendError`, `ConnectError`) | Python `Exception` / `RuntimeError`（Display 文本） |
| weakref | N/A | 不支持 `weakref.ref()`（PyO3 `#[pyclass]` 限制） |

---

## 运行验证

```bash
# 编译 Rust → Python binding
. "$HOME/.cargo/env" && cd py-arf && ../.venv/bin/python -m maturin develop

# 运行教学示例
cd py-arf && ../.venv/bin/python python/arf/examples/phase1_bus_hello.py

# 运行全部 Python 测试 (66 tests)
cd py-arf && ../.venv/bin/pytest tests/ -v
```

---

## 产出

| 文件 | 说明 |
|------|------|
| `docs/v1.x/task-1.12-docs-examples.md` | 本文档：Python API 完整参考 |
| `py-arf/python/arf/examples/phase1_bus_hello.py` | 教学示例：多节点聊天室 |
| `docs/v1.x/phase1-bus-design.md` | Phase 1 架构设计（已存在） |
