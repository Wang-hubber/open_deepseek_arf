# ARF Bus — Message Bus API Reference

> **Phase 1** · CAN-bus model · Single-channel broadcast + receiver-side filtering
>
> `pip install arf` · `from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch`

---

## Overview

`arf.Bus` 是 ARF 框架的消息总线，采用 CAN 总线模型：**单通道广播 + 接收侧过滤**。

```
                    ┌─────────────────────────────────┐
                    │           Bus (broadcast)         │
                    │  ┌─────────────────────────────┐ │
  engine/main ──────┼──┤  tokio broadcast channel    ├──┼────── trace/obs
                    │  │  (ring buffer, N slots)     │  │
  mcp/fs ───────────┼──┤                             ├──┼────── worker/1
                    │  └─────────────────────────────┘  │
  model/gpu-0 ──────┼───────────────────────────────────┼────── worker/2
                    │   Node A sends → all receivers    │
                    │   Each receiver filters locally   │
                    └─────────────────────────────────┘
```

### CAN Bus Model

| CAN 物理层 | ARF Bus |
|-----------|---------|
| 单根线缆，所有节点并联 | 一条 `tokio::sync::broadcast` channel |
| CAN ID Mask 硬件过滤 | `MessageFilter` — 接收侧按 `type` + `to` 过滤 |
| 帧级 ACK（至少有人收到） | `SendReceipt` — 在线节点数 + 匹配节点数 |
| 错误帧全体丢弃 | `Lagged(n)` — 慢消费者自己兜底 |
| 无中心路由 | Bus 只广播，不感知谁该收到什么 |

### When to Use Bus

**适合**：多节点协作（engine + mcp + model + trace）、广播/定向混合通信、节点动态上下线、需要心跳存活检测。

**不适合**：点对点 RPC（用 `jrpc`）、持久化消息队列（用外部 MQ）、需要精确一次投递保证的金融交易。

---

## Quickstart

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

async def main():
    # 1. Create Bus
    bus = Bus(heartbeat_interval_ms=5000, heartbeat_timeout_ms=15000, channel_capacity=64)

    # 2. Connect nodes
    engine = await bus.connect(
        NodeInfo("engine/main", "engine", {"role": "orchestrator"}),
        MessageFilter(),
    )
    worker = await bus.connect(
        NodeInfo("worker/1", "worker", {"gpu": 0}),
        MessageFilter(types=["job"]),
    )

    # 3. Send broadcast
    receipt = await engine.send("job", [], {"task": "train", "lr": 0.001})
    print(f"sent → online={receipt.online_nodes}, matching={receipt.matching_nodes}")

    # 4. Receive
    msg = await worker.recv()
    print(f"recv ← type={msg.msg_type}, payload={msg.payload}")

    # 5. Shut down
    await bus.shutdown()

asyncio.run(main())
```

**Expected output:**

```
sent → online=2, matching=2
recv ← type=job, payload={'task': 'train', 'lr': 0.001}
```

### Installation

```bash
cd py-arf && ../.venv/bin/python -m maturin develop
```

```python
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch
print(__version__)  # "1.0.0-alpha.0"
```

---

## Core Concepts

### Node Lifecycle

一个节点在 Bus 上的完整生命周期：

```
connect() → online → [send/recv] → disconnect() → offline
                ↓                        ↓
          heartbeat loop           node_offline broadcast
                ↓
          crash (no disconnect) → zombie entry → heartbeat timeout → evicted
```

**Zombie entry**: 如果 NodeHandle 被 Python GC 回收时未调用 `disconnect()`（例如进程崩溃），NodeEntry 残留为 zombie。心跳超时后自动清理，清理前同 NodeId 无法重连。

### Message Flow

```
  sender.send("job", [], {...})
       │
       ▼
  Bus: serde_json → Message { id: UUID, from: "engine/s", ... }
       │
       ▼
  broadcast_tx.send() ──→ ring buffer ──→ broadcast_rx (node A)
                                       ├─→ broadcast_rx (node B)
                                       └─→ broadcast_rx (node C)
                                              │
                                    recv() → filter.matches()?
                                              │ yes
                                              ▼
                                         return Message
```

### Filter Semantics

`MessageFilter` 是 **接收侧过滤**——消息总是广播给所有人，各节点自行决定是否接收。

```python
# Default: receive all types, both broadcast and directed-to-me
MessageFilter()

# Action-only worker
MessageFilter(types=["action", "event"])

# Session-bound node — only receives messages directed specifically to it
MessageFilter(to_match=ToMatch.DirectedToMe)

# Trace node — sees everything (including directed-to-others)
MessageFilter(types=None, to_match=ToMatch.All)
```

### `recv()` Execution Model

`recv()` 是 FIFO 阻塞接收，内部行为：

1. 调用 `broadcast_rx.recv()` 获取下一条消息
2. 如果是 `heartbeat_request` → 自动回复 ACK，**不返回给调用方**，回到步骤 1
3. 运行 `MessageFilter.matches(msg)` — 类型白名单 + to_match 检查
4. 匹配 → 返回 `Message`；不匹配 → 回到步骤 1

`try_recv()` 逻辑相同但不阻塞——无消息时返回 `None`。

!!! warning "Concurrent recv"
    同一 NodeHandle 同时只能有一个 `recv()` 或 `try_recv()` 在执行。并发调用抛 `RuntimeError("concurrent recv in progress")`。

### `message_count` Semantics

`Bus.message_count` 和 `BusGraph.message_count` 只统计**应用消息**（通过 `NodeHandle.send()` 发送）。`node_online`、`node_offline`、`heartbeat_request` 等 lifecycle 消息不计入。

---

## API Reference

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

A message bus based on CAN bus model. All messages are broadcast to all nodes;
each node filters locally via `MessageFilter`.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `heartbeat_interval_ms` | `int` | `1000` | How often the Bus sends heartbeat requests (ms). Shorter = faster failure detection but more overhead. |
| `heartbeat_timeout_ms` | `int` | `3000` | How long without ACK before a node is evicted (ms). Must be > `heartbeat_interval_ms` × 2 in practice. |
| `channel_capacity` | `int` | `16` | Broadcast ring buffer size. Messages beyond this capacity cause `Lagged(n)` on slow consumers. For high-throughput scenarios, set to 256+. |

**Raises:** None (constructor never fails).

**Example:**

```python
# Default: suitable for most scenarios
bus = Bus()

# Low-latency: fast heartbeat, small buffer
bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=300, channel_capacity=16)

# High-throughput: large buffer for bursty traffic
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

Register a node on the bus. Broadcasts `node_online` to all existing nodes,
then creates the node's broadcast receiver.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `info` | `NodeInfo` | Node identity and capabilities. `node_id` must be unique among currently online nodes. |
| `filter` | `MessageFilter` | Controls which messages this node receives. Default is `MessageFilter()` (all types, broadcast+directed). |

**Returns:** `NodeHandle` — the node's handle for sending and receiving.

**Raises:**

| Exception | Match text | Trigger |
|-----------|-----------|---------|
| `Exception` | `"already connected"` | Duplicate `NodeId` (including zombie entries) |
| `Exception` | `"bus closed"` | Bus has been shut down |

**Example:**

```python
engine = await bus.connect(
    NodeInfo("engine/main", "engine", {"session": "s1", "role": "orchestrator"}),
    MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
)
```

!!! note "broadcast_rx timing"
    The node's broadcast receiver is created **after** its `node_online` broadcast.
    This means a node never sees its own `node_online`, but sees subsequent nodes'
    `node_online` messages.

---

#### `Bus.shutdown()`

```python
async def shutdown(self) -> None:
    ...
```

Shut down the bus. Closes the broadcast channel — all pending `recv()` calls
unblock with `Closed`, all future `send()` calls raise `BusClosed`.

**This method is idempotent** — calling it multiple times has no additional effect.

**Example:**

```python
await bus.shutdown()

# After shutdown, all handles are broken
with pytest.raises(Exception):
    await handle.recv()
```

!!! warning "Buffered messages after shutdown"
    After `shutdown()`, `recv()` may still return messages that were already in the ring
    buffer. Drain buffered messages first, then verify `Closed`:

    ```python
    await bus.shutdown()
    while True:
        try:
            m = handle.try_recv()
            if m is None:
                break
        except Exception:
            break
    # Now recv should raise
    with pytest.raises(Exception):
        await handle.recv()
    ```

---

#### `Bus.graph()`

```python
def graph(self) -> BusGraph:
    ...
```

**Synchronous.** Returns a snapshot of the bus's current state.

**Returns:** `BusGraph` with `.nodes` (list of online `NodeInfo`), `.message_count` (application messages sent), `.uptime_ms`.

**Example:**

```python
g = bus.graph()
print(f"{len(g.nodes)} nodes, {g.message_count} messages, {g.uptime_ms}ms")
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

Total application messages sent since the bus was created. Lifecycle messages
(`node_online`, `node_offline`, `heartbeat_request`) are **not** counted.

---

#### `Bus.uptime_ms`

```python
@property
def uptime_ms(self) -> int:
    ...
```

Milliseconds since the bus was created.

---

### `NodeHandle`

```python
class NodeHandle:
    # No public constructor — obtained via Bus.connect()
    ...
```

A node's handle for sending and receiving messages. Created by `Bus.connect()`.

**All methods raise `RuntimeError("already disconnected")` after `disconnect()` is called.**

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

Send a message onto the bus. The `from` field is automatically set to this node's `node_id`.

**Args:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `msg_type` | `str` | Application-defined message type, e.g. `"action"`, `"job"`, `"tool_call"`. |
| `to` | `list[NodeId]` | Empty list `[]` = broadcast to all. `[NodeId("a"), NodeId("b")]` = directed to specific nodes. |
| `payload` | `Any` | JSON-serializable value (`dict`, `list`, `str`, `int`, `float`, `bool`, `None`). Non-serializable objects raise `ValueError`. |

**Returns:** `SendReceipt` with `.message_id` (UUID), `.online_nodes` (total online at send time), `.matching_nodes` (nodes whose filter could match this message).

**Raises:**

| Exception | Match text | Trigger |
|-----------|-----------|---------|
| `Exception` | `"target nodes offline"` | All specified targets are offline (directed send only; broadcast never triggers this) |
| `Exception` | `"bus closed"` | Bus has been shut down |
| `Exception` | `"bus buffer full"` | Broadcast ring buffer is full |
| `RuntimeError` | `"already disconnected"` | Handle has been disconnected |

**Example:**

```python
# Broadcast — all nodes with matching filter receive it
receipt = await handle.send("job", [], {"task": "train"})

# Directed — only specified nodes receive it (if their filter matches)
target = NodeId("mcp/fs")
receipt = await handle.send("tool_call", [target], {"tool": "read", "path": "/tmp/x"})

# Access receipt
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

Receive the next message matching this node's filter. **Blocks** until a matching
message arrives or the channel closes.

**Returns:** `Message` — the received message. Never returns `heartbeat_request`
(these are filtered and auto-acknowledged internally).

**Raises:**

| Exception | Match text | Trigger |
|-----------|-----------|---------|
| `RuntimeError` | `"already disconnected"` | Handle disconnected |
| `Exception` | `"recv error: Closed"` | Bus shut down, all buffered messages drained |
| `RuntimeError` | `"concurrent recv in progress"` | Another `recv()` or `try_recv()` is in progress |

**Example:**

```python
msg = await handle.recv()
print(msg.msg_type)        # "job"
print(msg.sender)          # NodeId('engine/main')
print(msg.payload)         # {'task': 'train'}
print(msg.is_broadcast())  # True
```

!!! note "recv() and node_online"
    When your node connects **after** other nodes are already online, `recv()` may
    first return `node_online` messages from nodes that connected before you.
    Drain these before expecting application messages.

---

#### `NodeHandle.try_recv()`

```python
def try_recv(self) -> Message | None:
    ...
```

**Synchronous, non-blocking.** Like `recv()` but returns `None` immediately if
no matching message is available.

**Returns:** `Message` if available, `None` otherwise.

**Raises:**

| Exception | Match text | Trigger |
|-----------|-----------|---------|
| `RuntimeError` | `"already disconnected"` | Handle disconnected |
| `RuntimeError` | `"concurrent recv in progress"` | Another `recv()` is in progress |
| `Exception` | `"try_recv error"` | Underlying broadcast receiver error |

**Example:**

```python
# Polling pattern
while True:
    msg = handle.try_recv()
    if msg is not None:
        print(f"got: {msg.msg_type}")
        break
    await asyncio.sleep(0.01)
```

---

#### `NodeHandle.disconnect()`

```python
async def disconnect(self) -> None:
    ...
```

Disconnect from the bus. Broadcasts `node_offline` to all online nodes, immediately
removes the entry from the nodes map.

**After calling this method, the handle is consumed** — all subsequent method calls
on this handle raise `RuntimeError("already disconnected")`.

**Raises:**

| Exception | Match text | Trigger |
|-----------|-----------|---------|
| `RuntimeError` | `"already disconnected"` | Handle already disconnected |

**Example:**

```python
await handle.disconnect()

# Same NodeId can reconnect immediately with a new handle
new_handle = await bus.connect(
    NodeInfo("worker/1", "worker", {}),
    MessageFilter(),
)
```

!!! warning "Disconnect vs Drop"
    If you let `NodeHandle` go out of scope without calling `await disconnect()`,
    the node becomes a **zombie entry** in the bus. It blocks reconnection with the
    same `NodeId` until the heartbeat timeout evicts it. Always call `await disconnect()`
    explicitly for controlled shutdown.

---

#### `NodeHandle.node_info()`

```python
def node_info(self) -> NodeInfo:
    ...
```

**Synchronous.** Return the `NodeInfo` this handle was created with.

---

#### `NodeHandle.filter_config()`

```python
def filter_config(self) -> MessageFilter:
    ...
```

**Synchronous.** Return the `MessageFilter` this handle was created with.

---

### `NodeId`

```python
class NodeId:
    def __init__(self, id: str) -> None:
        ...
```

Unique node identifier. Wraps a string with equality and hashing support.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| (internal) | `str` | The raw node ID string |

**Methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `__str__` | `() -> str` | Returns the raw node ID string, e.g. `"engine/main"` |
| `__repr__` | `() -> str` | Returns `NodeId('engine/main')` |
| `__eq__` | `(other: NodeId) -> bool` | String equality comparison |
| `__hash__` | `() -> int` | Hash of the string; usable as `dict` key / `set` member |

**Example:**

```python
a = NodeId("engine/main")
b = NodeId("engine/main")
assert a == b
assert hash(a) == hash(b)
assert str(a) == "engine/main"

# Usable in dicts and sets
registry = {a: "primary engine"}
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

Node identity and capabilities, registered with the bus at connect time.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `node_id` | `str` | (required) | Unique node identifier. Convention: `"type/name"`, e.g. `"engine/main"`, `"mcp/fs"`. |
| `node_type` | `str` | (required) | Node category: `"engine"`, `"mcp"`, `"model"`, `"trace"`, `"worker"`, etc. |
| `capabilities` | `Any` | (required) | Arbitrary JSON-serializable metadata: tools list, GPU info, session IDs, etc. |
| `online_since` | `int` | `0` | Unix millisecond timestamp. Default `0` means "not specified". |

**Attributes (read-only):**

| Attribute | Type | Description |
|-----------|------|-------------|
| `.node_id` | `NodeId` | The node's unique identifier |
| `.node_type` | `str` | The node's type category |
| `.capabilities` | `Any` | The node's capabilities (JSON-deserialized Python object) |
| `.online_since` | `int` | Unix millisecond timestamp |

**Example:**

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
    # No public constructor — obtained via recv() / try_recv()
    ...
```

A message received from the bus. Returned by `NodeHandle.recv()` and `NodeHandle.try_recv()`.

**Attributes (read-only):**

| Attribute | Type | Description |
|-----------|------|-------------|
| `.id` | `str` | UUID v4 message identifier |
| `.msg_type` | `str` | Application message type, e.g. `"job"`, `"action"` |
| `.sender` | `NodeId` | Sender's node ID |
| `.to` | `list[NodeId]` | Target node IDs (empty for broadcast) |
| `.payload` | `Any` | JSON-deserialized message body |
| `.timestamp` | `int` | Unix millisecond timestamp |

**Methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `.is_broadcast()` | `() -> bool` | Returns `True` if `to` is empty |
| `.is_for(node_id)` | `(node_id: NodeId) -> bool` | Returns `True` if `node_id` is in `to` |

**Example:**

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

Controls which messages a `NodeHandle` receives. Applied **per-node, receiver-side**.

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `types` | `list[str] \| None` | `None` | Message type whitelist. `None` = accept all types. `["action", "job"]` = only those types. |
| `to_match` | `ToMatch \| None` | `BroadcastAndDirectedToMe` | Target matching strategy. `None` defaults to `BroadcastAndDirectedToMe`. |

**Attributes (read-only):**

| Attribute | Type | Description |
|-----------|------|-------------|
| `.types` | `list[str] \| None` | The type whitelist |
| `.to_match` | `ToMatch` | The target matching strategy |

**Example:**

```python
# Worker that handles "job" and "infer" broadcasts + directed-to-me
f = MessageFilter(types=["job", "infer"])

# Trace node that sees EVERYTHING
f = MessageFilter(types=None, to_match=ToMatch.All)

# Service that only responds to direct calls (not broadcasts)
f = MessageFilter(types=None, to_match=ToMatch.DirectedToMe)
```

---

### `ToMatch`

```python
class ToMatch:
    All: ToMatch                       # Receive all messages (broadcast + directed to anyone)
    BroadcastOnly: ToMatch             # Only broadcast messages (to=[])
    DirectedToMe: ToMatch              # Only messages directed to this node
    BroadcastAndDirectedToMe: ToMatch  # Default: broadcast + directed to this node
```

Target matching strategy. Four singleton instances accessed as class attributes.

!!! note "ToMatch is not an Enum"
    `ToMatch` values are singleton instances, not Python `Enum` members. Compare with `==`:
    ```python
    assert ToMatch.All != ToMatch.BroadcastOnly
    assert f.to_match == ToMatch.BroadcastAndDirectedToMe
    ```

**Example:**

```python
from arf import ToMatch

# All four variants
ToMatch.All                       # "I see everything"
ToMatch.BroadcastOnly             # "I only care about broadcasts"
ToMatch.DirectedToMe              # "I only respond to direct calls"
ToMatch.BroadcastAndDirectedToMe  # "Default — broadcasts + calls to me"
```

---

### `SendReceipt`

```python
class SendReceipt:
    # No public constructor — returned by NodeHandle.send()
    ...
```

Acknowledgment returned by `NodeHandle.send()`. Confirms the message entered the
broadcast channel, not that any specific node received it.

**Attributes (read-only):**

| Attribute | Type | Description |
|-----------|------|-------------|
| `.message_id` | `str` | UUID v4 of the sent message |
| `.online_nodes` | `int` | Total online nodes at send time (including sender) |
| `.matching_nodes` | `int` | Online nodes whose filter potentially matches this message |

!!! note "matching_nodes is a lower bound"
    `matching_nodes` counts nodes whose `MessageFilter.types` includes the message's
    `msg_type` (or is `None`). It does **not** account for `to_match` —
    `DirectedToMe` nodes will still be counted even if the message is a broadcast.

---

### `BusGraph`

```python
class BusGraph:
    # No public constructor — returned by Bus.graph()
    ...
```

A point-in-time snapshot of bus health.

**Attributes (read-only):**

| Attribute | Type | Description |
|-----------|------|-------------|
| `.nodes` | `list[NodeInfo]` | Currently online nodes |
| `.message_count` | `int` | Application messages sent since bus creation |
| `.uptime_ms` | `int` | Milliseconds since bus creation |

**Example:**

```python
g = bus.graph()
assert len(g.nodes) >= 0
assert g.message_count >= 0
assert g.uptime_ms >= 0
```

---

## Common Patterns

### Worker Pool (Load Balancing at Application Layer)

Multiple workers of the same type all receive every broadcast. The application
layer decides which worker processes the job (e.g., via consistent hashing).

```python
async def worker_pool():
    bus = Bus(heartbeat_interval_ms=5000, heartbeat_timeout_ms=15000, channel_capacity=128)

    # Dispatcher sends jobs
    dispatcher = await bus.connect(
        NodeInfo("engine/dispatcher", "engine", {}),
        MessageFilter(),
    )

    # 4 GPU workers, all with identical filters
    workers = []
    for i in range(4):
        w = await bus.connect(
            NodeInfo(f"model/gpu-{i}", "model", {"gpu": i}),
            MessageFilter(types=["infer"]),
        )
        workers.append(w)

    # Broadcast one inference job — all 4 workers see it
    await dispatcher.send("infer", [], {"prompt": "hello"})

    for w in workers:
        msg = await w.recv()
        assert msg.payload == {"prompt": "hello"}

    await bus.shutdown()
```

!!! note "Bus does NOT load-balance"
    The bus broadcasts to all matching nodes. If only one worker should handle each
    job, implement application-level selection (round-robin, consistent hashing, or
    session affinity via `DirectedToMe`).

### Session Affinity

Route all messages for a specific session to the same worker using directed sends.

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

    # Send message specifically to session-1's worker
    target = NodeId("worker/session-1")
    await engine.send("tool_call", [target], {"tool": "read", "path": "/data/s1"})

    msg = await worker_s1.recv()
    print(msg.payload)  # {'tool': 'read', 'path': '/data/s1'}

    await bus.shutdown()
```

### Tracing / Observability

A trace node with `ToMatch.All` sees every message on the bus.

```python
async def tracing():
    bus = Bus(heartbeat_interval_ms=5000, heartbeat_timeout_ms=15000, channel_capacity=1024)

    # Trace node — sees everything
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

    # Engine broadcasts a job
    await engine.send("job", [], {"task": "compress"})

    # Trace sees it (ToMatch.All)
    trace_msg = await trace.recv()
    print(f"[trace] {trace_msg.msg_type} from {trace_msg.sender}")
    # → [trace] job from engine/main

    # Engine sends directed tool_call
    await engine.send("tool_call", [NodeId("mcp/fs")], {"tool": "read"})

    # Both trace AND worker see the directed message
    # (trace has ToMatch.All, worker's filter matches "tool_call")
    worker_msg = await worker.recv()
    trace_msg2 = await trace.recv()
    print(f"[worker] got {worker_msg.msg_type}")
    print(f"[trace]  got {trace_msg2.msg_type} (directed to {trace_msg2.to})")

    await bus.shutdown()
```

### Graceful Shutdown

```python
async def graceful_shutdown():
    bus = Bus()
    h1 = await bus.connect(NodeInfo("node-1", "test", {}), MessageFilter())
    h2 = await bus.connect(NodeInfo("node-2", "test", {}), MessageFilter())

    # Send some messages
    await h1.send("msg", [], {"n": 1})
    await h1.send("msg", [], {"n": 2})

    # 1. Disconnect all nodes first
    await h1.disconnect()
    await h2.disconnect()

    # 2. Then shutdown the bus
    await bus.shutdown()
```

Or, if you need to shutdown with nodes still connected:

```python
async def shutdown_with_nodes_online():
    bus = Bus()
    h = await bus.connect(NodeInfo("node-1", "test", {}), MessageFilter())

    await bus.shutdown()

    # Drain buffered messages, then recv raises
    while True:
        try:
            m = h.try_recv()
            if m is None:
                break
        except Exception:
            break

    with pytest.raises(Exception):
        await h.recv()
```

### Reconnect After Crash

```python
async def reconnect_after_crash():
    bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=300, channel_capacity=32)

    # Primary worker connects and crashes (no disconnect)
    async def crash():
        w = await bus.connect(
            NodeInfo("worker/main", "worker", {}),
            MessageFilter(),
        )
        # Simulated crash — no disconnect()

    await crash()

    # Reconnect immediately fails — zombie entry exists
    with pytest.raises(Exception, match="already connected"):
        await bus.connect(
            NodeInfo("worker/main", "worker", {}),
            MessageFilter(),
        )

    # Wait for heartbeat timeout to evict zombie (timeout_ms=300ms)
    await asyncio.sleep(0.5)

    # Now reconnect succeeds
    w2 = await bus.connect(
        NodeInfo("worker/main", "worker", {}),
        MessageFilter(),
    )
    print("reconnected after zombie eviction")

    await w2.disconnect()
    await bus.shutdown()
```

---

## Error Reference

| Exception Type | Match Text | Typical Cause |
|---------------|-----------|---------------|
| `Exception` | `"already connected"` | Duplicate `NodeId` or zombie entry not yet evicted |
| `Exception` | `"bus closed"` | Calling `connect()` or `send()` after `shutdown()` |
| `Exception` | `"target nodes offline"` | Directed send where all targets are offline |
| `Exception` | `"bus buffer full"` | Ring buffer exhausted; increase `channel_capacity` or slow sender |
| `Exception` | `"recv error: Closed"` | `recv()` after shutdown with empty buffer |
| `Exception` | `"try_recv error"` | `try_recv()` internal error (rare) |
| `RuntimeError` | `"already disconnected"` | Calling any method on a disconnected `NodeHandle` |
| `RuntimeError` | `"concurrent recv in progress"` | Calling `recv()` or `try_recv()` while another `recv()` is active |
| `RuntimeError` | `"concurrent access in progress"` | Concurrent `node_info()` / `filter_config()` calls |
| `ValueError` | — | `payload` or `capabilities` is not JSON-serializable |

---

## Python vs Rust API Differences

| Aspect | Rust (`arf-bus`) | Python (`arf.Bus`) |
|--------|-----------------|-------------------|
| `Bus::shutdown` | `async fn shutdown(self)` — consumes Bus | `async fn shutdown(&self)` — calls `signal_shutdown`, closes broadcast channel |
| `send()` error type | `Result<SendReceipt, SendError>` enum | Python `Exception` with `Display` text |
| `connect()` error type | `Result<NodeHandle, ConnectError>` enum | Python `Exception` with `Display` text |
| `NodeHandle` after disconnect | Compile-time check (consumed by `disconnect`) | Runtime `RuntimeError` |
| Payload type | `serde_json::Value` | `Any` (bridged via `json.dumps` / `json.loads`) |
| Async return type | `impl Future<Output = T>` | Python `Future` (not coroutine — use `ensure_future()`, not `create_task()`) |
| Weak references | N/A | Not supported (`TypeError: cannot create weak reference`) |

---

## See Also

- [Phase 1 Bus Design](../v1.x/phase1-bus-design.md) — Rust architecture and design decisions
- [Task 1.12 — Development Notes](../v1.x/task-1.12-docs-examples.md) — Full development task doc with all test code
- [Task 1.11 — Python Tests](../v1.x/task-1.11-python-tests.md) — 66-test suite with behavioral discoveries
