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

### 发现 4：`Lagged` 后 receiver 位置语义（#5 修复）

**现象**：容量 2 的 ring buffer，发 10 条消息后 slow 收到 `Lagged`，再发 1 条新消息后 `slow.recv()` 仍然返回 `Lagged(1)`。

**根因**：查阅 tokio broadcast 文档，`Receiver::recv()` 返回 `Lagged` 后，receiver 的 cursor **跳到 ring buffer 中"最老仍保留的消息"位置，不是 tail**。这意味着 Lagged 后 buffer 中还有可读的残留消息。

```
发 10 条消息，capacity=2:
  msg 1-8 被覆盖（丢失）
  msg 9, 10 保留在 ring buffer

slow.recv() → Lagged(n)  ← cursor 跳到 msg 9（最老保留）
slow.recv() → msg 9      ← 残留消息
slow.recv() → msg 10     ← 残留消息
slow.recv() → 阻塞       ← buffer 空
```

之前的测试在 Lagged 后直接 send + recv，但此时 buffer 还有 msg 9、10 没消费。除非新 send 覆盖了它们（又产生 Lag），否则 recv 先拿到的是残留的 msg 9，不是新消息。

**修复**：Lagged 后用 `try_recv()` 非阻塞循环 drain 所有残留消息，再发新消息验证恢复：

```rust
let mut saw_lag = false;
loop {
    match slow.try_recv() {
        Err(TryRecvError::Lagged(_)) => { saw_lag = true; }
        Err(TryRecvError::Empty) => break,
        Ok(_) => {} // drain buffered messages
    }
}
assert!(saw_lag, "should have seen Lagged after overflow");

// Now buffer is clean, new message arrives normally
bus.send(msg).await.unwrap();
assert_eq!(slow.recv().await.unwrap().payload, "fresh");
```

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
