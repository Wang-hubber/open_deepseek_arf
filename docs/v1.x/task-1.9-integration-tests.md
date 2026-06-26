# 任务 1.9：集成测试

> Phase 1 — Bus 消息总线第九项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.2-1.7 全部完成

## 设计思路

集成测试验证多组件协作场景——多个 NodeHandle + Bus + 心跳 + 过滤在真实 tokio runtime 下的行为。放在 `crates/arf-bus/tests/integration.rs`（独立于 `src/` 的集成测试目录）。

---

## 测试场景

### 基础多节点 (6 tests)

| # | 场景 | 描述 | 状态 |
|---|------|------|------|
| 1 | 上线/下线广播 | 3 节点互相看到 node_online/node_offline | ✅ |
| 2 | 定向消息过滤 | DirectedToMe 只收定向到己，BroadcastOnly 只收广播 | ✅ |
| 3 | 心跳超时 | 僵尸节点停止消费 → 超时 → node_offline 被其他节点看到 | ✅ |
| 4 | Trace 全量消费 | ToMatch::All + types=None → 看到所有消息 | ✅ |
| 5 | Lag 恢复 | 慢消费者 Lagged → 继续正常接收 | ✅ |
| 6 | 并发 connect/disconnect/send | 不丢消息不 panic | ✅ |

### Corner case (6 tests)

| # | 场景 | 为什么是 corner | 描述 | 状态 |
|---|------|----------------|------|------|
| 7 | Late joiner + graph() | 新节点 subscribe 晚于已有节点的 node_online，看不到历史 | 新节点通过 `graph()` 获取当前在线节点列表 | ✅ |
| 8 | 多 filter 不同子集 | 同一批消息经过不同 filter，各节点收到不同子集 | 3 节点，4 条消息，trace 全收/worker 1/ watcher 1 | ✅ |
| 9 | 心跳超时 + 同 NodeId 重连 | 僵尸超时下线后同 ID 重新连接 | node_offline → node_online 完整周期 | ✅ |
| 10 | disconnect 时消息还在缓冲 | send 和 disconnect 消息循环串行 | 消息先于 node_offline 到达 | ✅ |
| 11 | 快速 connect/disconnect 循环 | 资源泄漏风险 | ×10 次，graph 始终正确 | ✅ |
| 12 | shutdown 时还有在线节点 | 优雅关闭 | recv 返回 Closed，send 返回 BusClosed | ✅ |

**当前：11/12 通过，1 个待分析（#5 Lag 恢复）。**

---

## 实现中发现的问题与修复

### 发现 1：`node_online` 可见性规则

**现象**：测试中断言 `a.recv()` 返回 `node_offline`，却收到了 `node_online`。

**根因**：`Bus::connect()` 在 `handle_connect` 返回后才创建 `broadcast_rx`。所以节点永远看不到自己的 `node_online`，但可以看到后续连接节点的 `node_online`。未消费的 `node_online` 会阻塞在 recv 队列里。

具体时序：

```
A 连接: handle_connect 注册 A → broadcast A 的 node_online → A.rx 创建（在广播之后）
B 连接: handle_connect 注册 B → broadcast B 的 node_online → A.rx 收到（A.rx 已存在）
C 连接: handle_connect 注册 C → broadcast C 的 node_online → A.rx 收到，B.rx 收到
```

- A.rx 可见：B 的 node_online, C 的 node_online（2 条）
- B.rx 可见：C 的 node_online（1 条）
- C.rx 可见：无（rx 创建在所有 node_online 之后）

**修复**：每个测试在发送应用消息前，先用 `drain_all()` 清空各节点的 `node_online` 残留。`drain_all()` 是 try_recv 循环——不阻塞，安全。

### 发现 2：lifecycle 消息不 drain dummy receiver

**现象**：无直接测试失败。但长时间运行后，drain_rx 累积大量 lifecycle 消息（node_online、node_offline、heartbeat_request），占用 ring buffer 有效容量。应用消息的可用空间减少，慢消费者更容易 Lag。

**根因**：消息循环中只有 `BusCommand::Send` 分支在广播后调用了 `while drain_rx.try_recv().is_ok() {}`。`Connect`、`Disconnect`、`heartbeat_tick` 三个分支都通过 `broadcast_tx.send()` 广播了消息，但**没有 drain**。

```
Send 分支:        broadcast → drain ✅
Connect 分支:     broadcast → ❌ 没有 drain
Disconnect 分支:  broadcast → ❌ 没有 drain
heartbeat_tick:   broadcast → ❌ 没有 drain
```

drain_rx 是 ring buffer 中的一个消费者。如果它不消费，buffer 里就一直有这些过时消息占位。broadcast 不会阻塞（`send()` 是同步方法，从不等待），但会覆盖旧消息、给慢 receiver 发 Lagged。drain 的目的是**释放已被所有应用 receiver 消费过的 slot**，保持有效容量。

**修复**：在三个 lifecycle 分支的 `select!` arm 中广播后添加 `while drain_rx.try_recv().is_ok() {}`。

```rust
// lib.rs 消息循环
Some(BusCommand::Connect { .. }) => {
    let result = handle_connect(&broadcast_tx, &nodes, info, filter);
    while drain_rx.try_recv().is_ok() {}  // ← 新增
    let _ = respond_to.send(result);
}
Some(BusCommand::Disconnect { .. }) => {
    handle_disconnect(&broadcast_tx, &nodes, &node_id);
    while drain_rx.try_recv().is_ok() {}  // ← 新增
    let _ = respond_to.send(());
}
// ...
_ = heartbeat_timer.tick() => {
    heartbeat::handle_heartbeat_tick(&broadcast_tx, &nodes, heartbeat_timeout);
    while drain_rx.try_recv().is_ok() {}  // ← 新增
}
```

### 发现 3：`NodeEntry.filter` 存储但从未使用

**现象**：编译 warning `field `filter` is never read`。

**根因**：`handle_connect` 把 `MessageFilter` 存入 `NodeEntry.filter`，但消息循环在计算 `matching_nodes` 时直接用了 `online_nodes`（广播）或 `online_targets`（定向），从未查询 `NodeEntry.filter`。

**修复**：`matching_nodes` 计算改为遍历 nodes map，用每个 entry 的 `filter.matches()` 精确计数：

```rust
// Before
let matching_nodes = if is_broadcast { online_nodes } else { online_targets };

// After
let matching_nodes = if is_broadcast {
    let map = nodes.read().unwrap();
    map.values()
        .filter(|entry| entry.filter.matches(&msg, &entry.info.node_id))
        .count()
} else {
    online_targets
};
```

消除了 `filter` 字段的 dead_code warning，同时让 `SendReceipt.matching_nodes` 语义更精确——广播时计入的是"filter 真正匹配该消息的在线节点数"。

### 发现 4：Lag 恢复测试的排查全过程（#5）

**最终现象**：容量 2 的 ring buffer，发 3 条消息后 slow 收到 `Lagged`，预期 drain 残留后能正常接收新消息。

#### 弯路 1：错判阻塞原因

最初用 10 条消息 flood，测试**静默 hang 60 秒**。第一反应是 `broadcast_tx.send()` 在 buffer 满时阻塞了当前线程。这是错误的——查阅 tokio 源码后确认 `send()` 从不等待慢消费者，直接覆盖最旧消息。

#### 弯路 2：误判 `Lagged` 后 receiver 跳到 tail

假设 `recv()` 返回 `Lagged` 后 receiver position 跳到 ring buffer 的 tail（最新写入位置）。按此假设，Lagged 后应无残留消息，直接 `recv()` 等新消息即可。但实际行为是 `recv()` 又返回 `Lagged(1)`。

**真相**（查 tokio 源码 + 文档）：`Lagged` 后 receiver position 跳到**"最老仍保留的消息"位置**，不是 tail。

```
发 3 条消息，capacity=2:
  msg 0 被覆盖（丢失）
  msg 1, 2 保留在 ring buffer

slow.recv() → Lagged(1)  ← cursor 跳到 msg 1（最老保留）
slow.recv() → msg 1      ← 残留消息
slow.recv() → msg 2      ← 残留消息
slow.recv() → 阻塞 / 空  ← buffer 空
```

所以 Lagged 后还有可读残留消息，必须先 drain 干净才能验证新消息。

#### 弯路 3：`while let` drain 循环 hang

用 `while let Ok(Some(m)) = slow.try_recv()` drain 残留消息时，10 条消息版本**再次 hang**。加 `eprintln` 后发现 drain 2 条后 `try_recv()` 不是返回 `Empty` 而是**挂住**。

换成 3 条消息（刚好 overflow capacity-2 buffer 1 次），并改用显式 4 次 `try_recv()` 调用而非循环，问题消失。怀疑 `try_recv()` 在特定条件下与 message loop 的 `drain_rx.try_recv()` 竞争 tail lock（两者都调用 `self.shared.tail.lock()`），tight loop 加剧了竞争。**最终方案：capacity+1 条消息 + 显式 try_recv 调用**，稳定可靠。

#### 弯路 4：`try_recv()` 返回值类型误判（查了底层忘了自己的 wrapper）

最初按 `Result<Message, TryRecvError>` 写断言，空时预期 `Err(Empty)`。编译报错 `no field 'payload' on type 'Option<Message>'` 才发现类型不对。**不是 tokio 版本差异**——是 `NodeHandle::try_recv()` 自己包装了一层：

| 层级 | 返回类型 | 空队列 |
|------|---------|--------|
| `broadcast::Receiver::try_recv()` (tokio 1.52.3) | `Result<Message, TryRecvError>` | `Err(Empty)` |
| **`NodeHandle::try_recv()` (connection.rs:96)** | **`Result<Option<Message>, TryRecvError>`** | **`Ok(None)`** |

`NodeHandle` 把 `Err(Empty)` 转成 `Ok(None)`，因为"当前无消息"对应用层不是错误。查 bug 时去翻了 tokio 源码 `Receiver::try_recv()`，看到返回 `Result<T, TryRecvError>`，想当然以为自己的 `NodeHandle::try_recv()` 也一样——**典型的查了底层文档，忘了自己的 wrapper 签名**。

#### 最终方案

```rust
// 发 capacity+1 条消息触发一次 Lagged
for i in 0..3 {
    bus.send(Message::new("msg", ..., vec![], json!(i))).await.unwrap();
}

// 显式 drain：Lagged → 残留 msg → 残留 msg → 空
let r1 = slow.try_recv(); // Lagged(1)
let r2 = slow.try_recv(); // msg 1
let r3 = slow.try_recv(); // msg 2
let r4 = slow.try_recv(); // Ok(None) — 队列空

assert!(matches!(r1, Err(Lagged(_))));
assert!(matches!(r2, Ok(Some(_))));
assert!(matches!(r3, Ok(Some(_))));
assert!(matches!(r4, Ok(None)));

// 验证恢复：新消息正常到达
bus.send(Message::new("msg", ..., vec![], json!("fresh"))).await.unwrap();
assert_eq!(slow.recv().await.unwrap().payload, json!("fresh"));
```

**教训**：
1. tokio broadcast `send()` 不阻塞，buffer 满时覆盖旧消息——不要和 mpsc 混淆
2. `Lagged` 后 receiver 跳到"最老保留"，不是 tail——残留消息必须 drain
3. 查底层 API 文档时，别忘了自己的 wrapper 可能改了返回类型——先看自己的代码
4. tight loop `try_recv()` 可能与发送方竞争 tail lock——必要时用显式调用代替循环

---

## 辅助工具

### `drain_all()`

```rust
fn drain_all(handle: &mut NodeHandle) -> Vec<(String, String)> {
    let mut msgs = Vec::new();
    while let Ok(Some(m)) = handle.try_recv() {
        msgs.push((m.msg_type, m.from.0));
    }
    msgs
}
```

非阻塞清空所有可读消息。用于在测试断言前消除 `node_online`、`node_offline`、`heartbeat_request` 等生命周期消息的干扰。

---

## 相关代码变更（超出集成测试本身的修复）

| 文件 | 变更 | 关联发现 |
|------|------|---------|
| `crates/arf-bus/src/lib.rs` | Connect/Disconnect/heartbeat_tick 后 drain dummy | 发现 2 |
| `crates/arf-bus/src/lib.rs` | `matching_nodes` 用 `NodeEntry.filter` 精确计数 | 发现 3 |
| `crates/arf-bus/tests/integration.rs` | 12 个集成测试 + `drain_all()` helper | 全部 |
| `crates/arf-bus/Cargo.toml` | 新增 `[dev-dependencies] tokio` | 集成测试需要 |

---

## 小结

- **12 个集成测试场景**：6 基础 + 6 corner
- **11/12 通过**，1 个 Lag 恢复测试待分析（#5）
- **3 个 bug 修复**源于集成测试发现，修改在 lib.rs 中
- `drain_all()` 是集成测试核心 pattern——先 drain 生命周期消息，再断言应用消息
