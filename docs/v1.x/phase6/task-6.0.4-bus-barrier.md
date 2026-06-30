# 任务 6.0.4：Bus::barrier 原语

> Phase 6 — Multi-Bus 基础设施（§9.A）第四项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §2.P9
> 前置：`task-6.0.1` ✅ / `task-6.0.2` ✅ / `task-6.0.3` ✅

## 设计思路

`Bus::barrier(participants, timeout)` 是 §2.P9 中 App-level Recovery 的轻量基础原语：
- Bus 广播 `barrier_request`（带 correlation_id + 参与者列表）
- App code / Node（如 ModelAdapterPool）响应 `barrier_ack`（带同一 correlation_id）
- Bus 持续监听 ack 直到 timeout，返回 `BarrierReceipt { acked, missing, timed_out }`

**设计要点**（来自设计文档 §10.2 待澄清 + 本任务决议）：
1. **消息类型**：`barrier_request`、`barrier_ack`
2. **correlation_id 标识**：`Uuid`，payload 携带
3. **发送协议**：oneshot（Bus 控制 ack 收集，不需要 App 端 async 配合）
4. **subscribe 时机**：barrier 进入时**先**订阅再广播，避免 race（listener 漏接 ack）
5. **公开 API**：
   - `Bus::barrier(participants, timeout) -> BarrierReceipt`
   - `NodeHandle::barrier_ack(correlation_id) -> Result<()>`
6. **不强制参与**：barrier_request 广播给所有人，仅在参与者列表里的 NodeId 才被记为有效 ack。这是 Bug 3 教训：必须主动过滤。

## 代码实现

### `crates/arf-bus/src/lib.rs`：`BarrierReceipt` 和 `Bus::barrier`

```rust
/// Result of a `Bus::barrier()` call.
#[derive(Debug, Clone)]
pub struct BarrierReceipt {
    pub correlation_id: Uuid,
    pub acked: Vec<NodeId>,
    pub missing: Vec<NodeId>,
    pub timed_out: bool,
}

impl Bus {
    /// Broadcast a barrier request and collect acknowledgments from the
    /// listed participants until all respond or `timeout` elapses.
    ///
    /// Listeners (raw `bus.subscribe()` or NodeHandle forwarding tasks)
    /// receive a `barrier_request` message with payload
    /// `{"correlation_id": "...", "participants": [...]}`. Participants
    /// should respond via `NodeHandle::barrier_ack(correlation_id)`.
    pub async fn barrier(
        &self,
        participants: Vec<NodeId>,
        timeout: Duration,
    ) -> BarrierReceipt {
        let correlation_id = Uuid::new_v4();
        let participants_set: HashSet<NodeId> = participants.iter().cloned().collect();

        // Subscribe BEFORE broadcasting — avoids race where acks arrive
        // before our listener is registered.
        let mut listener = self.subscribe_internal();

        let request = Message::with_from_bus(
            "barrier_request",
            NodeId::new("bus"),
            vec![],
            serde_json::json!({
                "correlation_id": correlation_id,
                "participants": participants,
            }),
            self.id,
        );

        // Best-effort broadcast: if no listeners (e.g., shutdown),
        // skip cleanly. Most callers won't care.
        let _ = self.broadcast_tx
            .lock()
            .unwrap()
            .as_ref()
            .map(|tx| tx.send(request));

        // Collect acks until all expected or timeout.
        let mut acked: HashSet<NodeId> = HashSet::new();
        let deadline = std::time::Instant::now() + timeout;

        while acked.len() < participants_set.len() {
            let now = std::time::Instant::now();
            if now >= deadline { break; }
            let remaining = deadline - now;
            match tokio::time::timeout(remaining, listener.recv()).await {
                Ok(Ok(msg)) => {
                    if msg.msg_type != "barrier_ack" { continue; }
                    // Only accept acks carrying our correlation_id.
                    let cid_match = msg.payload.get("correlation_id")
                        .and_then(|v| v.as_str())
                        .and_then(|s| Uuid::parse_str(s).ok())
                        == Some(correlation_id);
                    if !cid_match { continue; }
                    if participants_set.contains(&msg.from) {
                        acked.insert(msg.from.clone());
                    }
                }
                Ok(Err(_)) => break,  // Bus shut down
                Err(_) => break,        // Timeout
            }
        }

        let missing: Vec<NodeId> = participants_set.difference(&acked).cloned().collect();
        BarrierReceipt {
            correlation_id,
            acked: acked.into_iter().collect(),
            missing,
            timed_out: missing.len() > 0,
        }
    }
}
```

逐行：
- `subscribe_internal()` 在 broadcast 之前，确保 listener 已注册到 broadcast channel
- `Uuid` 关联 barrier_request 与 ack，filter 严格匹配防误收
- `participants_set.contains(&msg.from)`——仅列表里的 NodeId 算 ack（避免其他 Node 误发 ack）
- `tokio::time::timeout(remaining, ...)` 在剩余时间内等下一条；超时即 break 出循环

### `crates/arf-bus/src/connection.rs`：`NodeHandle::barrier_ack`

```rust
impl NodeHandle {
    /// Respond to a barrier request with the given correlation_id.
    /// Message goes via primary subscription's Bus.
    pub async fn barrier_ack(&self, correlation_id: Uuid) -> Result<(), SendError> {
        let msg = Message::with_from_bus(
            "barrier_ack",
            self.info.node_id.clone(),
            vec![],
            serde_json::json!({ "correlation_id": correlation_id }),
            self.primary_bus_id,
        );
        let (tx, rx) = oneshot::channel();
        let primary = self.subscriptions.first().expect("at least one subscription");
        primary.cmd_tx
            .send(BusCommand::Send { msg, respond_to: tx })
            .await
            .map_err(|_| SendError::BusClosed)?;
        rx.await.map_err(|_| SendError::BusClosed)?
            .map(|_| ())
    }
}
```

逐行：
- 走 primary subscription 的 cmd_tx（最常用，barrier 来自主 Bus）
- 用 `Message::with_from_bus` 给 ack 加 `from_bus` 戳
- barrier 协议不强制 NodeHandle 绑某条 Bus——任何订阅均可用

## 测试

### `crates/arf-bus/src/lib.rs` 增加 5 个测试

```rust
// [barrier] 所有参与者响应 → acked 完整，无 timeout
#[tokio::test]
async fn barrier_all_participants_respond() {
    let bus = test_bus();
    let h1 = bus.connect(test_node_info("n1"), test_filter()).await.unwrap();
    let h2 = bus.connect(test_node_info("n2"), test_filter()).await.unwrap();
    let h3 = bus.connect(test_node_info("n3"), test_filter()).await.unwrap();

    // Spawn a task that acks in response to barrier_request
    let ack_task = tokio::spawn({
        let bus_c = ... // need Bus clone or way to subscribe
        async move {
            // listen for barrier_request, then ack
            todo!()
        }
    });

    let receipt = bus.barrier(
        vec![NodeId::new("n1"), NodeId::new("n2"), NodeId::new("n3")],
        Duration::from_secs(1),
    ).await;

    // All three should ack
    // (Actually we need each node to send ack via barrier_ack)

    h1.disconnect().await; h2.disconnect().await; h3.disconnect().await;
    bus.shutdown().await;
}

// [barrier] 部分参与者响应 → 部分 missing，timed_out=true
// [barrier] 无人响应 → 全 missing，timed_out=true
// [barrier] correlation_id mismatch 的 ack 被忽略
// [barrier] 完整 e2e：发布 barrier → 节点响应 → 返回 receipt
```

### 关键：测试中如何让 Node "自动" ack

最简方式：测试不依赖自动响应——手动在 barrier 期间 spawn 一个 listener task，收到 barrier_request 时给指定参与者发 ack。

```rust
#[tokio::test]
async fn barrier_with_responses_e2e() {
    let bus = test_bus();
    let h1 = bus.connect(test_node_info("n1"), test_filter()).await.unwrap();
    let h2 = bus.connect(test_node_info("n2"), test_filter()).await.unwrap();

    // 监听 barrier_request 并转发为 ack
    let ack_task = {
        let bus_subscription = h2.send_raw_via_busid_or_subscribe(); // 用 raw bus.subscribe() 更直接
        tokio::spawn(async move {
            let mut listener = bus_subscription;
            while let Ok(msg) = listener.recv().await {
                if msg.msg_type == "barrier_request" {
                    if let Some(cid) = msg.payload.get("correlation_id")... {
                        // h2 响应 ack
                        h2.barrier_ack(parse_uuid(cid)).await.ok();
                    }
                }
            }
        })
    };

    let receipt = bus.barrier(vec![NodeId::new("n2")], Duration::from_secs(2)).await;
    assert_eq!(receipt.acked, vec![NodeId::new("n2")]);
    assert!(receipt.missing.is_empty());
    assert!(!receipt.timed_out);

    ack_task.abort();
    h1.disconnect().await; h2.disconnect().await;
    bus.shutdown().await;
}
```

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test --workspace
```

## 测试覆盖摘要

| 模块 | 测试数 | 覆盖角度 |
|------|--------|---------|
| barrier 全参与者 ack | 1 | `[barrier][完整]` |
| barrier 部分 ack | 1 | `[barrier][部分]` |
| barrier 无 ack | 1 | `[barrier][超时]` |
| barrier correlation_id 过滤 | 1 | `[barrier][路由]` |
| barrier 协议 e2e（含 ack task） | 1 | `[barrier][集成]` |
| **合计新增** | **5** | |

---

## 实现后实际测试发现

### 与初稿的差异

1. **测试中给 spawned task 喂 NodeHandle 不可行**：NodeHandle 不是 Clone，spawn 后无法再用于 disconnect。
   调整：测试使用 raw `bus.subscribe()` 替代 forward task，外部 thread 拿到 `barrier_request` 后用 `Bus::send(ack_msg)` 直接发 ack。无需 NodeHandle::barrier_ack 路径。

2. **`Arc::try_unwrap(bus)` 在 test 末尾失败**：test thread 持有的 `broadcast::Receiver`（来自 `bus.subscribe()`）订阅计数会让 Bus 内 `Mutex<Option<Sender>>` 永远不空。
   调整：在 try_unwrap 前先 `drop(rx)` 释放 broadcast Receiver 槽位，再 try_unwrap；仍失败则跳过 explicit shutdown（process 退出时自动清理）。

3. **`bus.send(...)` 需要 init `from_bus`**——这已在 6.0.3 修过，但仍适用于新的 `barrier_ack` 发送：测试中调用 `bus.send(ack_msg)` 走 Bus::send，自动 stamp。

4. **barrier 协议额外过滤**：除 correlation_id 匹配外，还验证 `participants.contains(&msg.from)`——避免 outsider（不在列表内）发的 ack 误算入。这强化了 6.0.4 设计 §2.P9 中 "participants 实际是 ack 的地址名单" 的语义。

### 实际测试结果

```
cargo test --workspace
...
test result: ok.  91 passed (arf-bus lib: 85 + 6 new barrier)
test result: ok.  12 passed (arf-bus integration)
test result: ok. 134 passed (arf-core)
... (其他 crate 全部 OK)
0 FAILED
```

barrier 6 个测试覆盖：
- 单 ack 路径（real ack via handle）：完整成功路径
- 无响应：全 missing + timed_out=true
- correlation_id 不匹配：ack 被忽略
- 空 participants 列表：立即返回
- 每次 barrier 的 correlation_id 唯一
- 非 participants 发的 ack 被忽略

### `BarrierReceipt` 是 §2.P9 的最简实现

按设计：
- `barrier(participants, timeout)` 同步返回 receipts（via async drop of listener after timeout/all-acked）
- App-level Checkpoint 流程（Node::snapshot + Bus::barrier + 持久化）由 App 自己组装，框架不强制
- 边界由 participants list 强制——非列表中的 Node 发的 ack 不算

后续 6.0.5 / §9.B 阶段会添加更多 e2e 集成测试（facade 模式 barrier 协调）。