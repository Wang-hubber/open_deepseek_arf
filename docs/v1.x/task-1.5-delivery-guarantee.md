# 任务 1.5：发送方投递保证

> Phase 1 — Bus 消息总线第五项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.4 心跳检测

## 设计思路

任务 1.5 实现 `send()` 的投递保证，分两部分：

**A. 核心类型变更**：`Message.to` 从单目标升级为多目标

| 字段 | Before | After | 语义 |
|------|--------|-------|------|
| `Message.to` | `Option<NodeId>` | `Vec<NodeId>` | 空 = 广播；非空 = 定向目标列表 |
| `Message::is_broadcast()` | `self.to.is_none()` | `self.to.is_empty()` | |
| `Message::is_for()` | `to == node_id` | `to.contains(node_id)` | |
| `NodeHandle::send(to)` | `Option<NodeId>` | `Vec<NodeId>` | |
| `Message::new(to)` | `Option<NodeId>` | `Vec<NodeId>` | |
| `SendError::NodeOffline` | `(NodeId)` | `(Vec<NodeId>)` | 报告全部不在线目标 |

**B. 投递保证规则**：全部不在线才拒绝

```
send(action, to=[mcp/fs, mcp/web]) → 至少一个在线 → broadcast → SendReceipt
                                    → 全都不在线 → Err(SendError::NodeOffline([mcp/fs, mcp/web]))
```

**为什么是多目标？**

一个 ReAct step 可能涉及多个工具节点（如同时读文件 + 搜索），发送方需要指定多个目标接收者。单 `NodeId` 无法表达此意图。

**为什么全部不在线才拒绝？**

部分在线意味着消息有消费方，广播仍有意义。只有全部离线时广播才是纯浪费，应拒绝并让发送方尽早感知。

**Trace 不在 `to` 里**：

Trace 节点通过 `subscribe()` 监听全量广播——它在 CAN 总线上静默记录所有流量。Trace **不是** `to` 的目标，**不参与**在线性检查。否则只要有 Trace 在线，`NodeOffline` 永远不会触发，投递保证形同虚设。

---

## 代码实现

### 步骤 1：`crates/arf-core/src/lib.rs` — 类型变更

#### `Message` 结构体

```rust
pub struct Message {
    pub id: Uuid,
    pub msg_type: String,
    pub from: NodeId,
    /// Receiver targets. Empty = broadcast to all.
    pub to: Vec<NodeId>,
    pub payload: serde_json::Value,
    pub timestamp: u64,
}
```

#### `Message::new()` 签名

```rust
pub fn new(
    msg_type: impl Into<String>,
    from: NodeId,
    to: Vec<NodeId>,            // was Option<NodeId>
    payload: serde_json::Value,
) -> Self
```

#### `Message::is_broadcast()`

```rust
pub fn is_broadcast(&self) -> bool {
    self.to.is_empty()          // was self.to.is_none()
}
```

#### `Message::is_for()`

```rust
pub fn is_for(&self, node_id: &NodeId) -> bool {
    self.to.contains(node_id)   // was self.to == Some(node_id)
}
```

#### `SendError::NodeOffline`

```rust
pub enum SendError {
    NodeOffline(Vec<NodeId>),   // was NodeOffline(NodeId)
    BusFull,
    BusClosed,
}
```

#### `SendError::Display` 更新

```rust
Self::NodeOffline(ids) => {
    let names: Vec<_> = ids.iter().map(|id| id.as_str()).collect();
    write!(f, "target nodes offline: {}", names.join(", "))
}
```

---

### 步骤 2：`crates/arf-bus/src/lib.rs` — 投递检查

消息循环 `BusCommand::Send` 分支：广播前检查定向目标是否全离线。

```rust
Some(BusCommand::Send { msg, respond_to }) => {
    // Validate targets for directed messages
    if !msg.to.is_empty() {
        let map = nodes.read().unwrap();
        let all_offline: Vec<NodeId> = msg.to.iter()
            .filter(|target| !map.contains_key(target))
            .cloned()
            .collect();
        if all_offline.len() == msg.to.len() {
            // ALL targets offline — reject
            let _ = respond_to.send(Err(SendError::NodeOffline(all_offline)));
            continue;
        }
    }

    let msg_id = msg.id;
    let is_broadcast = msg.to.is_empty();
    let _ = broadcast_tx.send(msg);
    message_count.fetch_add(1, Ordering::Relaxed);
    while drain_rx.try_recv().is_ok() {}

    let online_nodes = nodes.read().unwrap().len();
    let matching_nodes = if is_broadcast {
        online_nodes
    } else {
        // Count online targets (already verified at least one is online)
        // We reuse the check: all targets that passed the offline check
        // are online, so matching_nodes = to.len() - offline count
        // But since we only reject when ALL offline, and we're here,
        // at least one is online. For simplicity, matching_nodes stays
        // as count of online targets.
        let map = nodes.read().unwrap();
        msg.to.iter().filter(|t| map.contains_key(t)).count()
    };
    let receipt = SendReceipt {
        message_id: msg_id,
        online_nodes,
        matching_nodes,
    };
    let _ = respond_to.send(Ok(receipt));
}
```

**注意**：`matching_nodes` 计算需要对 `to` 二次遍历（第一次在检查中）。但 `to` 通常只有 1-3 个目标，性能影响可忽略。如果担心，可以在第一次检查时记录 `online_count`。

实际上 `msg.to` 在第一次检查后还在（我们只是 `iter()` borrow），第二次检查时 `msg.to` 还没被 move。但 `msg.to` 在 `broadcast_tx.send(msg)` 中被 move。需要把 `to` 的信息在 move 前提取出来。

**简化方案**：在检查阶段同时计数在线目标数。

```rust
if !msg.to.is_empty() {
    let map = nodes.read().unwrap();
    let offline: Vec<NodeId> = msg.to.iter()
        .filter(|t| !map.contains_key(t))
        .cloned()
        .collect();
    if offline.len() == msg.to.len() {
        let _ = respond_to.send(Err(SendError::NodeOffline(offline)));
        continue;
    }
    // At least one target is online
    directed_targets_online = msg.to.len() - offline.len();
}
```

然后在 receipt 构造处用 `directed_targets_online`。

---

### 步骤 3：`crates/arf-bus/src/connection.rs` — NodeHandle::send() 签名

```rust
pub async fn send(
    &self,
    msg_type: &str,
    to: Vec<NodeId>,              // was Option<NodeId>
    payload: serde_json::Value,
) -> Result<SendReceipt, SendError>
```

---

### 步骤 4：所有内部消息构造处

`handle_connect`、`handle_disconnect`、`handle_heartbeat_tick` 中的 `Message::new(..., None, ...)` → `Message::new(..., vec![], ...)`。

---

## 单元测试

### arf-core 测试变更

`Message` 相关测试 `to` 字段变更：

| 原测试 | 变更 |
|--------|------|
| `message_is_broadcast_when_to_is_none` | → `message_is_broadcast_when_to_is_empty`：`to: vec![]` |
| `message_is_not_broadcast_when_to_is_some` | → `message_is_not_broadcast_when_to_is_nonempty`：`to: vec![NodeId::new("b")]` |
| `message_is_for_target` | `to: vec![target.clone()]`，`is_for(&target)` = true |
| `message_is_for_wrong_target` | `to: vec![NodeId::new("target")]`，`is_for(&other)` = false |
| `message_broadcast_is_not_for_anyone` | `to: vec![]`，`is_for(&any)` = false |
| `message_directed_to_self` | `to: vec![self_id.clone()]` |
| 序列化往返测试 | `"to": ["receiver"]` JSON 数组 |
| `send_error_node_offline` | 改为 `NodeOffline(vec![NodeId::new("a")])` |

### arf-bus 新增测试

```rust
// [投递] 定向多个目标全部在线 → 成功
#[tokio::test]
async fn directed_send_multi_target_all_online_succeeds() { }

// [投递] 定向多个目标全部不在线 → NodeOffline([a, b])
#[tokio::test]
async fn directed_send_all_targets_offline_fails() { }

// [投递] 定向多个目标部分在线 → 成功广播
#[tokio::test]
async fn directed_send_partial_targets_online_succeeds() { }

// [投递] 定向到在线节点 disconnect 后再发 → NodeOffline
#[tokio::test]
async fn directed_send_after_disconnect_fails() { }

// [投递] 广播消息 to=[] → 永远成功
#[tokio::test]
async fn broadcast_message_always_succeeds() { }
```

---

## 测试清单

| # | 角度 | 测试名 | 覆盖 |
|---|------|--------|------|
| 1 | `[投递]` | `directed_send_multi_target_all_online_succeeds` | 多目标全在线 → OK |
| 2 | `[投递]` | `directed_send_all_targets_offline_fails` | 多目标全离线 → NodeOffline |
| 3 | `[投递]` | `directed_send_partial_targets_online_succeeds` | 部分在线 → OK |
| 4 | `[投递]` | `directed_send_after_disconnect_fails` | disconnect 后再发 → NodeOffline |
| 5 | `[投递]` | `broadcast_message_always_succeeds` | 广播永不成 error |

---

## 对已有测试的影响（变更范围）

| 文件 | 影响测试数 | 变更类型 |
|------|-----------|---------|
| `arf-core/src/lib.rs` | ~15 | `to: None` → `to: vec![]`；`to: Some(id)` → `to: vec![id]` |
| `arf-bus/src/lib.rs` | ~8 | 同上 + helper 函数 `test_msg()` |
| `arf-bus/src/connection.rs` | ~12 | `send(..., None, ...)` → `send(..., vec![], ...)` |
| `arf-bus/src/heartbeat.rs` | ~2 | `Message::new(..., None, ...)` → `vec![]` |
| `arf-bus/src/lib.rs` 内部构造 | ~4 | `handle_connect` / `handle_disconnect` / `handle_heartbeat_tick` |

总计约 40+ 处引用需要从 `Option<NodeId>` 迁移到 `Vec<NodeId>`。

---

## 小结

- **`Message.to: Vec<NodeId>`** — 空 = 广播，非空 = 多目标定向
- **全部不在线才拒绝** — 部分在线则成功广播
- **Trace 不在 `to` 里** — Trace 通过 subscribe 监听，不参与在线性检查
- **`SendError::NodeOffline(Vec<NodeId>)`** — 报告全部离线目标
- **5 个新测试** + **~40 处已有引用适配**
