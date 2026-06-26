# 任务 1.5：发送方投递保证

> Phase 1 — Bus 消息总线第五项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.3 节点连接与断连（nodes map 已就绪）

## 设计思路

任务 1.5 实现 `send()` 的投递保证——定向消息目标不在线时拒绝发送（而非默默广播），以及 `SendReceipt.matching_nodes` 的精确计算。

**两个变更**：

| 变更 | 位置 | 说明 |
|------|------|------|
| 定向消息目标验证 | 消息循环 `BusCommand::Send` | `to=Some(target)` 时检查 target 是否在 nodes map，不在则返回 `SendError::NodeOffline` |
| `matching_nodes` 区分广播/定向 | 同上 | 广播 = online_nodes；定向 = 1（仅目标可能匹配） |

**为什么定向目标不在线要拒绝？**

CAN 模型下所有消息都在同一根"线"上广播，但定向消息的 `to` 字段表达了投递意图。如果目标不在线，广播出去白占带宽且无人处理。拒绝 + 返回 error 让发送方尽早知道投递失败，是防御性编程。

**对比当前行为**：

```
Before (1.3/1.4):
  send(action, to=mcp/filesystem) → 无论 mcp/filesystem 是否在线，一律 broadcast
  
After (1.5):
  send(action, to=mcp/filesystem) → 在线 → broadcast → SendReceipt { online_nodes: N, matching_nodes: 1 }
                                   → 不在线 → Err(SendError::NodeOffline(mcp/filesystem))
```

---

## 代码实现

### `crates/arf-bus/src/lib.rs` — 消息循环 Send 分支

在 `BusCommand::Send` 处理中，广播前增加目标在线性检查：

```rust
Some(BusCommand::Send { msg, respond_to }) => {
    // Validate target for directed messages
    if let Some(ref target) = msg.to {
        let map = nodes.read().unwrap();
        if !map.contains_key(target) {
            let _ = respond_to.send(Err(SendError::NodeOffline(target.clone())));
            continue; // skip to next loop iteration
        }
    }

    let msg_id = msg.id;
    let _ = broadcast_tx.send(msg);
    message_count.fetch_add(1, Ordering::Relaxed);
    while drain_rx.try_recv().is_ok() {}

    let online_nodes = nodes.read().unwrap().len();
    let matching_nodes = if msg.to.is_none() {
        online_nodes  // broadcast: all online nodes could match
    } else {
        1             // directed: only the target is expected to match
    };
    let receipt = SendReceipt {
        message_id: msg_id,
        online_nodes,
        matching_nodes,
    };
    let _ = respond_to.send(Ok(receipt));
}
```

逐行：
- `if let Some(ref target) = msg.to` — 仅在定向消息时检查。`ref` 避免 move `msg.to`
- `map.contains_key(target)` — O(1) 查找。`nodes.read()` 获取读锁，与并发 send/broadcast 共享
- `continue` — 跳过广播，直接返回下一循环迭代。`respond_to` 已被消费（send error）
- `matching_nodes` — 广播时仍为 `online_nodes`（1.6 实现 filter 匹配后精确到真正匹配 filter 的节点数）

**注意**：检查发生在读锁作用域内。如果 target 存在，读锁在 `if` 块结束时释放，广播阶段不持锁。

**注意**：后续消息循环中 `msg.to` 被 move 进 `broadcast_tx.send(msg)`。在检查阶段 `msg.to` 仅被 borrow，不 move。

---

## 单元测试

### `crates/arf-bus/src/lib.rs` tests 新增

```rust
// ═══════════════════════════════════════════════════════════════
// Send — 定向消息投递保证 (3 tests)
// ═══════════════════════════════════════════════════════════════

// [投递] 定向发送给在线节点 → 成功，matching_nodes=1
#[tokio::test]
async fn directed_send_to_online_node_succeeds() {
    let bus = test_bus();
    let sender = bus.connect(test_node_info("s"), test_filter()).await.unwrap();
    let _target = bus.connect(test_node_info("t"), test_filter()).await.unwrap();

    let receipt = sender
        .send("action", Some(NodeId::new("t")), serde_json::json!("hi"))
        .await
        .unwrap();
    assert_eq!(receipt.online_nodes, 2);  // sender + target
    assert_eq!(receipt.matching_nodes, 1); // directed: only target

    sender.disconnect().await;
    _target.disconnect().await;
    bus.shutdown().await;
}

// [投递] 定向发送给不在线节点 → SendError::NodeOffline
#[tokio::test]
async fn directed_send_to_offline_node_fails() {
    let bus = test_bus();
    let sender = bus.connect(test_node_info("s"), test_filter()).await.unwrap();
    // No target connected — "t" is offline

    let result = sender
        .send("action", Some(NodeId::new("t")), serde_json::json!("hi"))
        .await;
    assert!(matches!(result, Err(SendError::NodeOffline(ref id)) if id.as_str() == "t"));

    sender.disconnect().await;
    bus.shutdown().await;
}

// [投递] 广播消息 matching_nodes=online_nodes（不变）
#[tokio::test]
async fn broadcast_message_matching_nodes_equals_online_nodes() {
    let bus = test_bus();
    let sender = bus.connect(test_node_info("s"), test_filter()).await.unwrap();
    let _n2 = bus.connect(test_node_info("n2"), test_filter()).await.unwrap();

    let receipt = sender
        .send("action", None, serde_json::json!("all"))
        .await
        .unwrap();
    assert_eq!(receipt.online_nodes, 2);
    assert_eq!(receipt.matching_nodes, 2); // broadcast: everyone

    sender.disconnect().await;
    _n2.disconnect().await;
    bus.shutdown().await;
}
```

---

## 测试清单

| # | 角度 | 测试名 | 覆盖 |
|---|------|--------|------|
| 1 | `[投递]` | `directed_send_to_online_node_succeeds` | 定向在线 → OK，matching_nodes=1 |
| 2 | `[投递]` | `directed_send_to_offline_node_fails` | 定向不在线 → NodeOffline |
| 3 | `[投递]` | `broadcast_message_matching_nodes_equals_online_nodes` | 广播 matching_nodes=online_nodes |

---

## 对已有测试的影响

- `directed_message_broadcast_to_all`（1.2）：to=Some("mcp/filesystem") 但无节点注册 → 现在会返回 `NodeOffline`。需要修改：先 connect 目标节点，再发定向消息。
- `node_handle_send_message_appears_on_bus`（1.3）：to=None → 无影响
- 其他 send 测试均使用 to=None → 无影响

---

## 小结

- **定向目标不在线** → `SendError::NodeOffline`，不广播，立即返回
- **定向目标在线** → 正常广播，`matching_nodes=1`
- **广播消息** → `matching_nodes=online_nodes`（1.6 精确到 filter 匹配数）
- **3 个新测试**，1 个已有测试需修改
