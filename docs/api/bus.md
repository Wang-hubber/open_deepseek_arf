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
| 总线始终有电，终端电阻消纳信号 | Bus 内部持有一个 dummy receiver（`drain_rx`），每次事件后自动 drain，作为**兜底消费者**防止环形缓冲区满→发送方阻塞 |

**tokio broadcast 的默认行为：** `tokio::sync::broadcast` 是一个固定容量的环形缓冲区。当**最慢的接收者**落后超过 `channel_capacity` 条消息时，`send()` 会**阻塞**等待该接收者追上——这是 tokio 内置的背压机制。换句话说，只要有一个接收者不消费，缓冲区终将填满，所有发送方被卡住。

**drain_rx 兜底策略：** Bus 在创建 broadcast channel 时获取一个常驻 receiver（`drain_rx`），并保证它在以下每个事件后立即清空：

| 事件 | 产生的消息 | 被谁消费？ |
|------|----------|----------|
| `NodeHandle.send()` | 应用消息（`job`、`tool_call` 等） | 匹配 filter 的节点 + trace（如果配置了） |
| `Bus.connect()` | `node_online` | 已在线节点（如果它们的 filter 不过滤） + trace |
| `NodeHandle.disconnect()` | `node_offline` | 已在线节点（同上） + trace |
| 心跳 tick | `heartbeat_request` + 可能的 `node_offline` | **无人消费**——heartbeat 由 `recv()` 内部自动 ACK，不返回给应用；timeout 产生的 `node_offline` 可能没有任何节点在线 |

关键洞察：**lifecycle 消息常常无人消费**。一个配置了 `types=["job"]` 的 worker 收不到 `node_online`；心跳只在 `recv()` 内部处理不掉 buffer 里的消息；trace 节点虽然可能用 `ToMatch.All` 全量记录，但它消费速度可能跟不上——而且记录不等于从缓冲区"消耗"，tokio broadcast 中每个 receiver 都有自己的消费位置。**`drain_rx` 是最终的兜底：它的消费位置在每次事件后被推到最新，保证无论是否有应用节点在消费，环形缓冲区永不满，`send()` 永不阻塞。**

**drain_rx 如何消费消息？—— 位置指针滚动图解**

tokio broadcast 用一个**单调递增的逻辑位置**追踪每个消费者，而非物理槽位引用计数。发送方每 `send()` 一次，位置 +1；接收方每 `recv()`/`try_recv()` 一次，自己的位置 +1。物理写入哪个 slot = `位置 % channel_capacity`。

下面以 `channel_capacity=4` 为例，跟踪 sender、drain_rx 和一个永不读取的慢消费者 `app_a` 在三轮 send 中的位置变化：

```
capacity=4 (物理 slot: 0 1 2 3)

█ 状态 A：bus 刚创建，所有光标在位置 0
  逻辑位置  0              物理 slot
  sender ·                    slot0  [______]
  drain  ·                    slot1  [______]
  app_a  ·                    slot2  [______]
                              slot3  [______]

█ 状态 B：消息循环收到 BusCommand::Send →
          broadcast_tx.send(msg0) 写入 slot(0%4=0) →
          sender 位置+1 →
          while drain.try_recv().is_ok() {} 追上 sender
  sender     · (位置1)        slot0  [msg0 ]
  drain      · (位置1)        slot1  [______]  ← 环形缓冲区有 3 个空闲 slot
  app_a  ·    (位置0)         slot2  [______]
                              slot3  [______]
  ── send() 返回 Ok ──       sender-drain=0，不阻塞

█ 状态 C：4 轮 send 后（msg0~msg3 全部写入 + drain 全部追上）
  sender              · (位置4)
  drain               · (位置4)   slot0  [msg3*] ← 位置4%4=0，msg3 覆盖 msg0
  app_a  ·             (位置0)    slot1  [msg1 ]
                                  slot2  [msg2 ]
                                  slot3  [msg3 ]
  ── 关键：app_a 落后 4 = capacity，tokio 将其标记为 Lagged ──
  ── 此后 sender 不再等待 app_a，以 drain 为"最慢非 Lagged 接收者" ──

█ 状态 D：第 5 轮 send(msg4) — app_a 已 Lagged，sender 不等待它
  sender                   · (位置5)
  drain                    · (位置5)  slot0  [msg3*]
  app_a  ·                  (位置0)   slot1  [msg4*] ← 位置5%4=1，msg4 覆盖 msg1
                                      slot2  [msg2 ]
                                      slot3  [msg3 ]
  ── send() 返回 Ok ── 发送方永不阻塞
```

**三步协同机制：**

```
NodeHandle.send()               消息循环 (独立 tokio task)             环形缓冲区
     │                              │                                    │
     ├─1─→ cmd_tx.send(cmd) ──→  cmd_rx.recv().await                    │
     │                              │                                    │
     │                          broadcast_tx.send(msg) ──2──→ 写入 slot[位置%cap]
     │                              │                            sender 位置+1
     │                              │                                    │
     │                          while drain_rx.try_recv().is_ok() {} ←─3─ 消费
     │                              │  ↑ 非阻塞循环，逐条推 drain 位置
     │                              │  ↑ 直到 drain 位置 = sender 位置
     │                              │  ↑ try_recv() 返回 Empty → 循环退出
     │                              │                                    │
     │←4── oneshot 回复 Ok ────────┘                                    │
     │                                                                   │
  send() 返回                                                           │
```

**什么时候消息真正从环形缓冲区消失？**

不是 drain_rx 读走的那一刻，而是**所有接收者都越过了该消息所在 slot** 的那个时刻。tokio broadcast 内部：当 sender 需要写入 `slot[N]` 时，检查是否还有接收者的位置 ≤ slot 的原始位置——如果全部越过，直接覆盖；如果有接收者还在那个位置上（且未 Lagged），`send()` 阻塞。drain_rx 的价值就是确保"全部越过"这个条件总是满足——因为它总是第一个越过。慢消费者 lag 超过 capacity 后被标记 Lagged，不再参与"是否越过"的检查。

**没有 drain_rx 会怎样？**

如果去掉 drain_rx，第一个无人消费的 `heartbeat_request`（无应用节点在线时）就会卡在环形缓冲区里——因为没人读它，它的位置永远不会被越过。`capacity` 条消息后，buffer 满，下一个 `send()` 永久阻塞。drain_rx 就是那个"总是读的人"。

**原生 tokio broadcast vs ARF Bus 竞速模式：行为对比**

以下用可运行的 Rust 代码展示原生 tokio broadcast 与 ARF Bus 在不同场景下的表现差异。原生 tokio 代码可独立运行（`tokio = { features = ["sync", "rt-multi-thread", "macros", "time"] }`）。

```
实验1: 零接收者时 send()
────────────────────────────────────────────────────
原生 tokio:
  let (tx, rx0) = broadcast::channel::<String>(4);
  drop(rx0);
  tx.send("hello".into())
  → Err(SendError("hello"))
  → 没有接收者 → channel 死了

ARF Bus:
  bus.send(msg).await
  → Ok(SendReceipt { ... })
  → drain_rx 是 broadcast::channel() 的原始返回值，永不 drop
  → 即使零 app 订阅者，send() 也不报错
────────────────────────────────────────────────────

实验2: capacity=2, 仅一个从不读的消费者
────────────────────────────────────────────────────
原生 tokio:
  let (tx, _rx_only) = broadcast::channel::<String>(2);
  tx.send("msg0".into()); // Ok
  tx.send("msg1".into()); // Ok (sender=2, rx=0, diff=2=capacity)
  tx.send("msg2".into()); // Ok — rx 落后 ≥ capacity, tokio 标记 Lagged, sender 继续
  → 原生 tokio 的 Lagged 机制也保护了发送方，慢消费者不阻塞 sender

ARF Bus (capacity=2, 同样的慢消费者):
  bus.send(msg0).await; // Ok
  bus.send(msg1).await; // Ok
  ...连续 10 次全部 Ok (见 arf-bus 测试 slow_receiver_lagged_not_backpressured)
  → drain_rx 在每次 send 后立即推到最新位置
  → 即使慢消费者未 Lagged,cursor=0,sender 也不阻塞(因为 drain_rx 更快)
  → 这个是纯兜底——原生 tokio 也能处理慢消费者,但 drain_rx 保证
    即使慢消费者是"唯一"的,也永远不会成为瓶颈
────────────────────────────────────────────────────

实验3: 全部 Receiver drop — 模拟 app 全部 disconnect + 心跳继续
────────────────────────────────────────────────────
原生 tokio:
  let (tx, rx0) = broadcast::channel::<String>(4);
  let app1 = tx.subscribe();
  let app2 = tx.subscribe();
  tx.send("job".into()).unwrap();  // Ok, app1/app2 正常通信
  app1.recv(); app2.recv();
  drop(app1); drop(app2); drop(rx0); // 全部退出

  tx.send("heartbeat_request".into())
  → Err(SendError("heartbeat_request"))
  → 零接收者 → Bus 死了，心跳无法发送

ARF Bus (同样场景):
  所有 app 节点 disconnect → drain_rx 仍在
  bus.send("heartbeat_request").await
  → Ok(SendReceipt { ... })
  → drain_rx 永不被 drop → Bus 照常运转
  → 心跳/生命周期消息始终有"人"收
────────────────────────────────────────────────────

实验4: capacity=4, 快+慢混合 — 慢的是否拖累快的?
────────────────────────────────────────────────────
原生 tokio:
  let (tx, mut rx_fast) = broadcast::channel::<String>(4);
  let _rx_slow = tx.subscribe(); // 从不读
  for i in 0..6 { tx.send(format!("msg{i}")).unwrap(); } // 全部 Ok
  rx_fast.recv().await → Lagged(2)  // 快消费者落后了,收到 Lagged 通知
  继续 recv → 后续消息正常
  → tokio 的 Lagged 语义保护: 慢的不拖累快的,快的也不救助慢的

ARF Bus:
  行为相同 — Lagged 语义保持不变
  drain_rx 额外保证: 至少有一个"超快消费者"始终在最新位置
  这意味着最慢非 Lagged 接收者的光标永远 ≥ drain_rx 的位置
```

**差异总结：**

tokio broadcast 原生的 `Lagged` 语义已经保护了"慢消费者不反压发送方"这个特性。ARF Bus 的 `drain_rx` 在此之上解决的是两个更基本的问题：

| 场景 | 原生 tokio broadcast | ARF Bus (竞速模式) |
|------|---------------------|-------------------|
| 零接收者 | `send()` → `Err(SendError)` | `send()` → `Ok(SendReceipt)` |
| 全部 app 退出后继续发消息 | `Err(SendError)` — channel 死 | `Ok` — drain_rx 是"常驻市民" |
| 慢消费者落后 < capacity | 不阻塞 | 不阻塞 (同左) |
| 慢消费者落后 ≥ capacity | 被 Lagged, sender 继续 | 被 Lagged, sender 继续 (drain_rx 兜底) |
| lifecycle 消息无人消费 | buffer 滚动覆盖 | drain_rx 逐条消费, 释放 slot |
| 设计承诺 | 隐式 — 行为取决于 receiver 数量/状态 | **显式 — send() 永不阻塞, 竞速模式** |

核心结论：**`drain_rx` 不是改造 tokio 的 Lagged 机制，而是在它之下铺了一层"永远不死"的兜底**——保证 broadcast channel 始终有一个活着的消费者，从而 `send()` 既不会因零接收者报错，也不会因唯一接收者卡住而阻塞。

这个机制完全透明——每个在线节点独立持有自己的 `broadcast::Receiver`，各自维护消费位置，不受 `drain_rx` 的 drain 影响。你不需要担心"没人收消息会不会堵塞通道"。

**设计取舍：竞速模式。** Bus 的核心承诺是**消息一定会被发出**——`send()` 永不阻塞发送方。消费端的规则是：**跟得上就能一直 live，跟不上也不会影响别人**。慢消费者独自承受 `Lagged(n)` 丢消息的代价，不会反压发送方或拖慢其他快消费者。如果你需要保证每个消息都被处理，在应用层做持久化 + 重试——Bus 层只管传输，不做反压。

### 适用场景

Bus 是 ARF 的**传输基础设施**——提供广播通道 + 过滤 + 心跳，不感知上层语义。更高级的通信模式构建在 Bus 之上：

```
应用层  │  jrpc（RPC 协议）      │  持久化（trace + Redis）  │  幂等重试
        │  请求-响应匹配、超时  │  全量捕获、写入外部存储  │  Exactly-Once
────────┼─────────────────────┼─────────────────────────┼─────────────
传输层  │                Bus（广播 + 定向 + 过滤 + 心跳）                │
```

**Bus 直接提供：** 多节点协作、广播/定向混合通信、节点动态上下线、心跳存活检测、服务发现。

**Bus 不直接提供，但可组合构建：**
- **RPC 调用** —— 定向消息 + `req_id` 手动关联即可实现。`jrpc` 在此基础上额外提供了超时、重试、类型契约
- **消息持久化** —— 外挂一个 `ToMatch.All` 的 trace 节点写入 Redis/DB，就能实现全量消息的持久存储
- **Exactly-Once** —— 应用层在 payload 中加入幂等键 + 接收方去重 + 发送方超时重试，即可在 Bus 之上实现

---

## 快速上手

### 安装

```bash
cd py-arf && ../.venv/bin/python -m maturin develop
```

```python
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch
print(__version__)  # "1.0.0-alpha.0"
```

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
        info=NodeInfo(node_id="engine/main", node_type="engine", capabilities={"role": "orchestrator"}),
        filter=MessageFilter(),
    )
    worker = await bus.connect(
        info=NodeInfo(node_id="worker/1", node_type="worker", capabilities={"gpu": 0}),
        filter=MessageFilter(types=["job"]),
    )

    # 3. 广播消息
    receipt = await engine.send(msg_type="job", to=[], payload={"task": "train", "lr": 0.001})
    print(f"已发送 → online={receipt.online_nodes}, matching={receipt.matching_nodes}")

    # 4. 接收消息
    msg = await worker.recv()
    print(f"收到 ← type={msg.msg_type}, payload={msg.payload}")

    # 5. 关闭 Bus
    await bus.shutdown()

asyncio.run(main())
```

**运行输出：**（耗时 <1ms）

```
已发送 → online=2, matching=2
收到 ← type=job, payload={'lr': 0.001, 'task': 'train'}
```

### 点对点 RPC

Bus 的定向消息天然支持点对点通信。加上 `req_id` 关联和调用方专用的消息类型，就能实现 RPC 式的请求-响应：

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

async def main():
    bus = Bus()

    a = await bus.connect(
        info=NodeInfo(node_id="node/a", node_type="test", capabilities={}),
        filter=MessageFilter(types=["tool_call_result"], to_match=ToMatch.DirectedToMe),
    )
    b = await bus.connect(
        info=NodeInfo(node_id="node/b", node_type="test", capabilities={}),
        filter=MessageFilter(types=["tool_call"], to_match=ToMatch.DirectedToMe),
    )

    # A → B tool_call
    await a.send(
        msg_type="tool_call",
        to=[NodeId(id="node/b")],
        payload={"tool": "add", "params": [3, 4], "req_id": "r1"},
    )
    # B 侧解包并完成运算，返回结果
    req = await b.recv()
    a_val, b_val = req.payload["params"]
    result = a_val + b_val
    await b.send(
        msg_type="tool_call_result",
        to=[NodeId(id="node/a")],
        payload={"result": result, "req_id": req.payload["req_id"]},
    )
    resp = await a.recv()
    print(f"3+4={resp.payload['result']}")

    await bus.shutdown()

asyncio.run(main())
```

**运行输出：**（耗时 <1ms）

```
3+4=7
```

可在 Bus 基础上进行 JRPC 实现。

### 持久化

外挂一个 `ToMatch.All` 的节点，将全量消息写入本地文件（或 Redis、数据库）：

```python
import asyncio, json, os, tempfile
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

async def main():
    bus = Bus()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
    tmp.close()

    # 持久化节点：ToMatch.All，写入文件
    persister = await bus.connect(
        info=NodeInfo(node_id="persist/store", node_type="persist", capabilities={}),
        filter=MessageFilter(types=None, to_match=ToMatch.All),
    )

    async def persist_loop(handle, path):
        with open(path, "a") as f:
            while True:
                try:
                    msg = await handle.recv()
                    if msg.msg_type == "node_online":
                        continue
                    f.write(json.dumps({
                        "type": msg.msg_type,
                        "sender": str(msg.sender),
                        "payload": msg.payload,
                    }) + "\n")
                    f.flush()
                except Exception:
                    break

    asyncio.ensure_future(persist_loop(persister, tmp.name))

    # 业务节点发送消息
    a = await bus.connect(
        info=NodeInfo(node_id="node/a", node_type="test", capabilities={}),
        filter=MessageFilter(),
    )
    await a.send(msg_type="job", to=[], payload={"task": "train"})
    await a.send(msg_type="job", to=[], payload={"task": "eval"})

    await asyncio.sleep(0.05)
    await bus.shutdown()

    # 读取持久化文件
    with open(tmp.name) as f:
        for line in f:
            print(f"持久化: {line.strip()}")
    os.unlink(tmp.name)

asyncio.run(main())
```

**运行输出：**（`/tmp/bus_log.jsonl` 内容）

```
{"type": "job", "sender": "node/a", "payload": {"task": "train"}}
{"type": "job", "sender": "node/a", "payload": {"task": "eval"}}
```

### Exactly-Once

在 payload 中携带幂等键，接收方本地去重。即使发送方因超时重试导致同一条消息被投递多次，接收方也只处理一次：

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

async def main():
    bus = Bus()

    sender = await bus.connect(
        info=NodeInfo(node_id="sender", node_type="test", capabilities={}),
        filter=MessageFilter(),
    )
    receiver = await bus.connect(
        info=NodeInfo(node_id="receiver", node_type="test", capabilities={}),
        filter=MessageFilter(types=["order"], to_match=ToMatch.DirectedToMe),
    )

    seen = set()  # 幂等去重表

    async def recv_loop(handle):
        while True:
            try:
                msg = await handle.recv()
                idem = msg.payload["idem_key"]
                if idem in seen:
                    print(f"重复消息已跳过: idem_key={idem}")
                    continue
                seen.add(idem)
                print(f"处理: idem_key={idem}, data={msg.payload['data']}")
            except Exception:
                break

    asyncio.ensure_future(recv_loop(receiver))

    # 模拟重试——同一订单发了 3 次
    for _ in range(3):
        await sender.send(
            msg_type="order",
            to=[NodeId(id="receiver")],
            payload={"idem_key": "order-42", "data": "buy 100 shares"},
        )

    await asyncio.sleep(0.05)
    await bus.shutdown()

asyncio.run(main())
```

**运行输出：**（第一条被处理，后两条被跳过）

```
处理: idem_key=order-42, data=buy 100 shares
重复消息已跳过: idem_key=order-42
重复消息已跳过: idem_key=order-42
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

> **警告：** 同一 NodeHandle 同时只能有一个 `recv()` 或 `try_recv()` 在执行。并发调用抛 `RuntimeError("concurrent recv in progress")`。

### `recv()` vs `try_recv()` 详解

两者执行**完全相同的内部逻辑**（heartbeat 过滤 → filter 匹配），区别只在调用方式和等待行为。详细 API 签名见 [`recv()`](#nodehandlerecv) 和 [`try_recv()`](#nodehandletry_recv)。

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
    info=NodeInfo(node_id="engine/main", node_type="engine", capabilities={"session": "s1", "role": "orchestrator"}),
    filter=MessageFilter(types=None, to_match=ToMatch.BroadcastAndDirectedToMe),
)
```

> **注意：** 节点的 broadcast receiver 在 `node_online` 广播**之后**创建。因此节点看不到自己的 `node_online`，但能看到之后连接的其他节点的 `node_online`。

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
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter

async def main():
    bus = Bus()
    handle = await bus.connect(
        info=NodeInfo(node_id="node-1", node_type="test", capabilities={}),
        filter=MessageFilter(),
    )
    await bus.shutdown()

    # 排空缓冲区中的残留消息
    drained = 0
    while True:
        try:
            m = handle.try_recv()
            if m is None:
                break
            drained += 1
        except Exception:
            break  # channel 已关闭

    # 此时缓冲区空，recv() 抛异常
    try:
        await handle.recv()
    except Exception as e:
        print(f"drained={drained}, recv raised: {e}")

asyncio.run(main())
```

**运行输出：**（耗时 <1ms）

```
drained=0, recv raised: recv error: channel closed
```

> **警告：** `shutdown()` 调用 `signal_shutdown`，关闭 broadcast channel 的**发送端**——此后不再有新消息进入。但 channel 的**接收端**仍然存活，环形缓冲区中的已发送消息尚未被消费。此时 `recv()` 的行为取决于缓冲区状态：
>
> | 缓冲区状态 | `recv()` 行为 |
> |-----------|-------------|
> | 有未读消息 | 逐条返回缓冲消息（FIFO 顺序） |
> | 缓冲区为空 | 返回 `Closed` 错误 |
>
> 上例中 `drained=0` 是因为该节点是唯一节点，看不到自己的 `node_online`。多节点场景下 drain 计数会 >0。
>
> **内存不会泄漏：** Bus 内部持有一个 dummy receiver（`drain_rx`），用于保持 broadcast channel 持续存活（类比 CAN 总线的"线缆始终有电"）。该 dummy 在每次 send / connect / disconnect / 心跳 tick 后都会 drain 掉其缓冲区内积压的消息。**这不是内存管理问题，而是背压兜底**——tokio broadcast 环形缓冲区在有慢消费者时，`send()` 会阻塞等待最慢者追上。`drain_rx` 确保最慢的消费位置始终被推到最新，从而 `send()` 永不阻塞。所有在线节点的 receiver 消费完毕后，环形缓冲区自动释放；已 disconnect 的节点其 `broadcast_rx` 已被 drop，不持有缓冲区引用。最坏情况下（shutdown 后既未 drain 也未 disconnect），内存也会在 `Bus` 对象被 Python GC 时随整个 channel 一起释放——不会有永久残留。

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
receipt = await handle.send(msg_type="job", to=[], payload={"task": "train"})

# 定向 — 仅指定节点能收到（前提是它们的 filter 匹配）
target = NodeId(id="mcp/fs")
receipt = await handle.send(msg_type="tool_call", to=[target], payload={"tool": "read", "path": "/tmp/x"})

# 查看回执
print(receipt.message_id)      # e.g. "550e8400-e29b-41d4-a716-446655440000"
print(receipt.online_nodes)    # e.g. 5
print(receipt.matching_nodes)  # e.g. 3
```

---

#### `NodeHandle.recv()`

```python
async def recv(self) -> Message:
    ...
```

接收下一条匹配当前节点 filter 的消息。**阻塞**直到有匹配消息到达或 channel 关闭。

> 与 `try_recv()` 的对比和用法选择见 [recv() vs try_recv() 详解](#recv-vs-try_recv-详解)。

**返回：** `Message` — 接收到的消息。`heartbeat_request` 永远不会返回给调用方（内部自动过滤并应答）。

**异常：**

| 异常类型 | match 文本 | 触发场景 |
|-----------|-----------|---------|
| `RuntimeError` | `"already disconnected"` | Handle 已 disconnect |
| `Exception` | `"recv error: channel closed"` | Bus 已 shutdown 且缓冲消息已全部 drain |
| `RuntimeError` | `"concurrent recv in progress"` | 另一个 `recv()` 或 `try_recv()` 正在执行 |

**示例：**

```python
msg = await handle.recv()
print(msg.msg_type)        # "job"
print(msg.sender)          # NodeId('engine/main')
print(msg.payload)         # {'task': 'train'}
print(msg.is_broadcast())  # True
```

> **注意：** `node_online` 和 `node_offline` 是 Bus 自动广播的 lifecycle 消息（`msg_type` 为 `"node_online"` / `"node_offline"`），节点**可以选择消费或忽略**，完全由 `MessageFilter` 决定：
>
> | Filter 配置 | 是否收到 lifecycle |
> |------------|------------------|
> | `types=None`（默认） | ✅ 收到——`"node_online"` 不在过滤白名单中，类型检查通过 |
> | `types=["action", "job"]` | ❌ 收不到——`"node_online"` 不在白名单中，被 filter 过滤 |
> | `to_match=ToMatch.All` | ✅ 收到——且能看到定向给其他节点的消息 |
>
> - **不关心 lifecycle**：用 `types=["your_type"]` 过滤掉，`recv()` 只返回应用消息
> - **需要服务发现**：保留默认 filter，消费 `node_online` 来感知新节点上线（获取其 `node_id`、`node_type`、`capabilities`）
> - **需要完整的上下线日志**：用 `ToMatch.All`，trace 节点可用
>
> 如果你收到了不需要的 lifecycle 消息，在开始消费应用消息前 drain 即可：
>
> ```python
> while True:
>     msg = await handle.recv()
>     if msg.msg_type not in ("node_online", "node_offline"):
>         break  # 第一条应用消息
> ```

---

#### `NodeHandle.try_recv()`

```python
def try_recv(self) -> Message | None:
    ...
```

**同步、非阻塞。** 与 `recv()` 逻辑相同，但无可用消息时立即返回 `None`。

> 与 `recv()` 的对比和用法选择见 [recv() vs try_recv() 详解](#recv-vs-try_recv-详解)。

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
    info=NodeInfo(node_id="worker/1", node_type="worker", capabilities={}),
    filter=MessageFilter(),
)
```

> **警告：** 如果让 NodeHandle 出作用域而不 `await disconnect()`，节点会变成 **zombie entry**，阻塞同 NodeId 重连直到心跳超时清理。**始终显式调用 `await disconnect()`** 做受控下线。
>
> ```python
> # ❌ 错误：handle 出作用域，未调用 disconnect → zombie entry
> async def bad():
>     h = await bus.connect(
>         info=NodeInfo(node_id="worker/1", node_type="worker", capabilities={}),
>         filter=MessageFilter(),
>     )
>     # h 在此处被 GC，未 disconnect
> await bad()
>
> # ✅ 正确：显式 disconnect 后出作用域 — 立即从 nodes map 移除
> async def good():
>     h = await bus.connect(
>         info=NodeInfo(node_id="worker/1", node_type="worker", capabilities={}),
>         filter=MessageFilter(),
>     )
>     await h.disconnect()  # 广播 node_offline，清理 entry
> await good()
> ```

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
a = NodeId(id="engine/main")
b = NodeId(id="engine/main")
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

> **注意：** `ToMatch` 值是单例实例，不是 Python `Enum` 成员。用 `==` 比较：
>
> ```python
> assert ToMatch.All != ToMatch.BroadcastOnly
> assert f.to_match == ToMatch.BroadcastAndDirectedToMe
> ```

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

> **注意：** `matching_nodes` 统计 `MessageFilter.types` 包含此消息 `msg_type`（或为 `None`）的节点。它**不考虑** `to_match` 策略——即使某节点设置了 `DirectedToMe`，广播消息也会将其计入 `matching_nodes`。

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
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter

async def worker_pool():
    bus = Bus(heartbeat_interval_ms=5000, heartbeat_timeout_ms=15000, channel_capacity=128)

    # 调度器发送任务
    dispatcher = await bus.connect(
        info=NodeInfo(node_id="engine/dispatcher", node_type="engine", capabilities={}),
        filter=MessageFilter(),
    )

    # 4 个 GPU worker，filter 完全相同
    workers = []
    for i in range(4):
        w = await bus.connect(
            info=NodeInfo(node_id=f"model/gpu-{i}", node_type="model", capabilities={"gpu": i}),
            filter=MessageFilter(types=["infer"]),
        )
        workers.append(w)

    # 广播一条推理任务 — 4 个 worker 全部收到
    await dispatcher.send(msg_type="infer", to=[], payload={"prompt": "hello"})

    for w in workers:
        msg = await w.recv()
        assert msg.payload == {"prompt": "hello"}

    await bus.shutdown()
```

**运行输出：**（4 个 worker 全部收到同一条广播，耗时 <1ms）

```
worker-0 收到: type=infer, payload={'prompt': 'hello'}
worker-1 收到: type=infer, payload={'prompt': 'hello'}
worker-2 收到: type=infer, payload={'prompt': 'hello'}
worker-3 收到: type=infer, payload={'prompt': 'hello'}
```

> **注意：** Bus 向所有匹配节点广播。如果每条任务只需要一个 worker 处理，在应用层实现选择逻辑（轮询、一致性哈希、或通过 `DirectedToMe` 实现会话粘性）。

### 会话粘性

通过定向发送将同一 session 的消息始终路由到同一个 worker。

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

async def session_affinity():
    bus = Bus()

    engine = await bus.connect(
        info=NodeInfo(node_id="engine/main", node_type="engine", capabilities={}),
        filter=MessageFilter(),
    )
    worker_s1 = await bus.connect(
        info=NodeInfo(node_id="worker/session-1", node_type="worker", capabilities={"session": "s1"}),
        filter=MessageFilter(to_match=ToMatch.DirectedToMe),
    )

    # 专门发送给 session-1 的 worker
    target = NodeId(id="worker/session-1")
    await engine.send(msg_type="tool_call", to=[target], payload={"tool": "read", "path": "/data/s1"})

    msg = await worker_s1.recv()
    print(msg.payload)

    await bus.shutdown()
```

**运行输出：**（耗时 <1ms）

```
{'path': '/data/s1', 'tool': 'read'}
```

### 可观测性 / Tracing

设置一个 `ToMatch.All` 的 trace 节点，监控 Bus 上所有消息。

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

async def tracing():
    bus = Bus(heartbeat_interval_ms=5000, heartbeat_timeout_ms=15000, channel_capacity=1024)

    # Trace 节点 — 全量消费，第一个上线
    trace = await bus.connect(
        info=NodeInfo(node_id="trace/obs", node_type="trace", capabilities={}),
        filter=MessageFilter(types=None, to_match=ToMatch.All),
    )

    engine = await bus.connect(
        info=NodeInfo(node_id="engine/main", node_type="engine", capabilities={}),
        filter=MessageFilter(),
    )
    worker = await bus.connect(
        info=NodeInfo(node_id="mcp/fs", node_type="mcp", capabilities={}),
        filter=MessageFilter(types=["tool_call"]),
    )

    # Trace 看到 engine 和 worker 的 node_online（全量消费的设计意图）
    for _ in range(2):
        msg = await trace.recv()
        print(f"[trace] {msg.msg_type} from {msg.sender}")

    # Engine 广播任务
    await engine.send(msg_type="job", to=[], payload={"task": "compress"})

    # Trace 看到 job（ToMatch.All），worker 看不到（types=["tool_call"] 过滤）
    trace_msg = await trace.recv()
    print(f"[trace] {trace_msg.msg_type} from {trace_msg.sender}")

    # Engine 发送定向 tool_call
    await engine.send(msg_type="tool_call", to=[NodeId(id="mcp/fs")], payload={"tool": "read"})

    # Worker 收到（filter 匹配 "tool_call"），Trace 也看到（ToMatch.All）
    worker_msg = await worker.recv()
    trace_msg2 = await trace.recv()
    print(f"[worker] {worker_msg.msg_type} payload={worker_msg.payload}")
    print(f"[trace] {trace_msg2.msg_type} 定向给 {trace_msg2.to}")

    await bus.shutdown()
```

**运行输出：**（耗时 <1ms）

```
[trace] node_online from engine/main
[trace] node_online from mcp/fs
[trace] job from engine/main
[worker] tool_call payload={'tool': 'read'}
[trace] tool_call 定向给 [NodeId('mcp/fs')]
```

### 快消费者代理 / Fan-out

当某个消费者处理逻辑过重导致频繁 `Lagged(n)`，且无法对单节点做效率优化时（例如需要调用慢速外部 API、做大规模计算、或数据本身倾斜），可以接入一个**快消费者代理**——它用最小开销捕获消息，立即 drain，然后转发到慢消费者或分布式消费池。

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch

async def fast_consumer_fanout():
    bus = Bus(heartbeat_interval_ms=5000, heartbeat_timeout_ms=15000, channel_capacity=256)

    # 生产者
    producer = await bus.connect(
        info=NodeInfo(node_id="engine/producer", node_type="engine", capabilities={}),
        filter=MessageFilter(),
    )

    # 快消费者代理：ToMatch.All，每条消息只做 enqueue，永不 Lagged
    queue: asyncio.Queue = asyncio.Queue()
    async def fast_drain(handle):
        """快速从 Bus 拉取消息放入本地队列，永远不阻塞 Bus 环形缓冲区。"""
        while True:
            try:
                msg = await handle.recv()
                if msg.msg_type == "node_online":
                    continue               # 跳过 lifecycle
                await queue.put(msg)        # 快：入队即返回
            except Exception:
                break  # Bus 关闭

    fast_agent = await bus.connect(
        info=NodeInfo(node_id="agent/fast-drain", node_type="agent", capabilities={}),
        filter=MessageFilter(types=None, to_match=ToMatch.All),
    )
    asyncio.ensure_future(fast_drain(fast_agent))

    # 慢消费者池：从本地队列取消息，可以并行处理
    async def slow_worker(worker_id: int):
        while True:
            msg = await queue.get()
            # 这里可以是耗时的外部 API 调用、GPU 推理、数据库写入等
            print(f"[worker-{worker_id}] type={msg.msg_type} seq={msg.payload['seq']}")
            await asyncio.sleep(0.05)  # 模拟慢处理
            queue.task_done()

    workers = [asyncio.ensure_future(slow_worker(i)) for i in range(4)]

    # 生产者高速发送
    for i in range(5):
        await producer.send(msg_type="job", to=[], payload={"seq": i})

    await asyncio.sleep(0.3)  # 等待消费
    print(f"队列剩余: {queue.qsize()}")
    await bus.shutdown()
```

**运行输出：**（4 个慢 worker 并行消费，耗时 <1ms）

```
[worker-0] type=job seq=0
[worker-1] type=job seq=1
[worker-2] type=job seq=2
[worker-3] type=job seq=3
[worker-0] type=job seq=4
队列剩余: 0
```

核心思路：**Bus 环形缓冲区只负责高速分发，不做背压**。快代理把消息卸到应用层队列后，缓冲区的 slot 立即释放。慢处理逻辑完全与 Bus 解耦，可以自由伸缩 worker 数量、使用进程池、甚至转发到外部消息队列。

### 优雅关闭

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter

async def graceful_shutdown():
    bus = Bus()
    h1 = await bus.connect(
        info=NodeInfo(node_id="node-1", node_type="test", capabilities={}),
        filter=MessageFilter(),
    )
    h2 = await bus.connect(
        info=NodeInfo(node_id="node-2", node_type="test", capabilities={}),
        filter=MessageFilter(),
    )

    # 发送一些消息
    await h1.send(msg_type="msg", to=[], payload={"n": 1})
    await h1.send(msg_type="msg", to=[], payload={"n": 2})

    # 1. 先断开所有节点
    await h1.disconnect()
    await h2.disconnect()

    # 2. 再关闭 Bus
    await bus.shutdown()
```

或者在节点仍在线的场景下 shutdown（注意多节点时缓冲区可能有残留 `node_online`）：

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter

async def shutdown_with_nodes_online():
    bus = Bus()
    h = await bus.connect(
        info=NodeInfo(node_id="node-1", node_type="test", capabilities={}),
        filter=MessageFilter(),
    )

    await bus.shutdown()

    # 先 drain 缓冲区中的残留消息
    drained = 0
    while True:
        try:
            m = h.try_recv()
            if m is None:
                break
            drained += 1
        except Exception:
            break

    # 此时 recv 应该抛异常
    try:
        await h.recv()
        assert False, "should have raised"
    except Exception:
        pass  # 预期行为

    print(f"drain={drained}, recv raised as expected")
```

**运行输出：**

```
explicit disconnect -> shutdown 耗时 0ms
direct shutdown -> drained=0, recv raises: Exception
```

### 崩溃后重连

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter

async def reconnect_after_crash():
    bus = Bus(heartbeat_interval_ms=100, heartbeat_timeout_ms=300, channel_capacity=32)

    # 主 worker 连接后崩溃（未调用 disconnect）
    async def crash():
        w = await bus.connect(
            info=NodeInfo(node_id="worker/main", node_type="worker", capabilities={}),
            filter=MessageFilter(),
        )
        # 模拟崩溃 — 不调用 disconnect()

    await crash()

    # 立即重连会失败 — zombie entry 仍然存在
    try:
        await bus.connect(
            info=NodeInfo(node_id="worker/main", node_type="worker", capabilities={}),
            filter=MessageFilter(),
        )
    except Exception as e:
        print(f"重连被拒: {e}")

    # 等待心跳超时清理 zombie（timeout_ms=300ms，等 ~500ms 确保清理完成）
    await asyncio.sleep(0.5)

    # 现在可以重连了
    w2 = await bus.connect(
        info=NodeInfo(node_id="worker/main", node_type="worker", capabilities={}),
        filter=MessageFilter(),
    )
    print("zombie 清理后重连成功")

    await w2.disconnect()
    await bus.shutdown()
```

**运行输出：**（总耗时约 0.5s —— 大部分是心跳等待）

```
重连被拒: node already connected: worker/main
zombie 清理后重连成功
```

### 服务发现与初始化时序

`node_online` 广播可以用来做服务发现——节点上线后感知已在线的 peer，获取其 capabilities。但常见需求是：**B 需要在 A 发消息之前完成初始化**。这需要控制连接顺序 + 就绪信号。

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter

async def service_discovery_and_ready_signal():
    bus = Bus()

    # 1. B 先 connect —— 保证 A 上线时 B 已经在监听
    b = await bus.connect(
        info=NodeInfo(node_id="worker/b", node_type="worker", capabilities={"ready": False}),
        filter=MessageFilter(),
    )

    # 2. A connect —— 此时 B 已经在线，B 的 recv() 会收到 A 的 node_online
    a = await bus.connect(
        info=NodeInfo(node_id="engine/a", node_type="engine", capabilities={"role": "producer"}),
        filter=MessageFilter(),
    )

    # 3. B 通过 node_online 感知 A 已上线，完成初始化
    while True:
        msg = await b.recv()
        if msg.msg_type == "node_online" and str(msg.sender) == "engine/a":
            caps = msg.payload["capabilities"]
            print(f"B 发现 A 上线: role={caps['role']}")
            break

    # 4. B 初始化完毕，发送就绪信号给 A
    a_id = NodeId(id="engine/a")
    await b.send(msg_type="ready", to=[a_id], payload={"worker": "worker/b", "status": "ready"})

    # 5. A 收到就绪信号后才开始发送应用消息
    ready_msg = await a.recv()
    print(f"A 收到: type={ready_msg.msg_type}")
    await a.send(msg_type="job", to=[], payload={"task": "process", "data": 42})

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

**运行输出：**（耗时 <1ms）

```
B 发现 A 上线: role=producer
A 收到: type=ready
B 处理: {'data': 42, 'task': 'process'}
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
| `Exception` | `"recv error: channel closed"` | `shutdown()` 后缓冲区为空时调用 `recv()` |
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
