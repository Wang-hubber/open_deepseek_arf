# 任务 1.6：接收侧过滤

> Phase 1 — Bus 消息总线第六项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.3 NodeHandle、任务 1.5 多目标 to

## 设计思路

任务 1.6 在 `NodeHandle.recv()`/`try_recv()` 内部实现 `MessageFilter` 过滤，让节点只接收自己关心的消息。

**过滤发生在 heartbeat 过滤之后**：

```
recv() loop:
  1. broadcast_rx.recv()  → 拿到下一条消息
  2. heartbeat_request?   → auto-ack, continue
  3. matches_filter()?    → no → continue (静默跳过)
  4. return msg           → 返回给应用层
```

**MessageFilter 两维度**：

| 维度 | 字段 | 逻辑 |
|------|------|------|
| 类型过滤 | `types: Option<Vec<String>>` | `None` = 全收；`Some(list)` = 仅 msg_type 在 list 中 |
| 目标过滤 | `to_match: ToMatch` | 见下方 |

**ToMatch 语义**（适配 `Vec<NodeId>` 的 `to`）：

| 变体 | msg 通过条件 |
|------|-------------|
| `All` | 全部通过 |
| `BroadcastOnly` | `msg.to.is_empty()` |
| `DirectedToMe` | `msg.to.contains(&my_id)` |
| `BroadcastAndDirectedToMe` | `msg.to.is_empty() \|\| msg.to.contains(&my_id)` |

**节点的 self.node_id 来源**：`NodeHandle.info.node_id`，connect 时设置。

---

## 代码实现

### `crates/arf-bus/src/filter.rs`（新文件）

```rust
//! Message filter — type and target-based receive-side filtering.
//!
//! `MessageFilter` controls which messages a `NodeHandle` delivers to
//! its application layer. Heartbeat messages bypass the filter and are
//! always intercepted.

use arf_core::{Message, MessageFilter, ToMatch};

impl MessageFilter {
    /// Returns true if the message passes this filter.
    ///
    /// - `types`: if `Some`, the message's `msg_type` must be in the list.
    /// - `to_match`: controls how `msg.to` is matched against `node_id`.
    pub(crate) fn matches(&self, msg: &Message, node_id: &arf_core::NodeId) -> bool {
        // 1. Type filter
        if let Some(ref types) = self.types {
            if !types.contains(&msg.msg_type) {
                return false;
            }
        }

        // 2. Target filter
        match self.to_match {
            ToMatch::All => true,
            ToMatch::BroadcastOnly => msg.to.is_empty(),
            ToMatch::DirectedToMe => msg.to.contains(node_id),
            ToMatch::BroadcastAndDirectedToMe => {
                msg.to.is_empty() || msg.to.contains(node_id)
            }
        }
    }
}
```

逐行：
- `pub(crate) fn matches` — crate 内可见，仅 NodeHandle 调用
- Type 检查先执行——O(n) 线性扫描 `types` 列表。`types` 通常小（1-5 项），性能影响可忽略
- `ToMatch::BroadcastAndDirectedToMe` — 默认配置，Engine 节点常用：收广播 + 定向到自己的消息
- `ToMatch::All` — Trace 节点常用：全收
- `ToMatch::DirectedToMe` — MCP 节点常用：只收定向调用
- `ToMatch::BroadcastOnly` — 仅关心系统广播的节点

### `crates/arf-bus/src/connection.rs` — recv() 增加 filter 调用

```rust
pub async fn recv(&mut self) -> Result<Message, broadcast::error::RecvError> {
    loop {
        let msg = self.broadcast_rx.recv().await?;
        if msg.msg_type == "heartbeat_request" {
            let _ = self.cmd_tx
                .send(BusCommand::HeartbeatAck {
                    node_id: self.info.node_id.clone(),
                })
                .await;
            continue;
        }
        if !self.filter.matches(&msg, &self.info.node_id) {
            continue;
        }
        return Ok(msg);
    }
}
```

try_recv() 同理：

```rust
pub fn try_recv(&mut self) -> Result<Option<Message>, broadcast::error::TryRecvError> {
    loop {
        match self.broadcast_rx.try_recv() {
            Ok(msg) => {
                if msg.msg_type == "heartbeat_request" {
                    let _ = self.cmd_tx.try_send(BusCommand::HeartbeatAck {
                        node_id: self.info.node_id.clone(),
                    });
                    continue;
                }
                if !self.filter.matches(&msg, &self.info.node_id) {
                    continue;
                }
                return Ok(Some(msg));
            }
            Err(broadcast::error::TryRecvError::Empty) => return Ok(None),
            Err(e @ broadcast::error::TryRecvError::Lagged(_)) => return Err(e),
            Err(broadcast::error::TryRecvError::Closed) => {
                return Err(broadcast::error::TryRecvError::Closed)
            }
        }
    }
}
```

### `crates/arf-bus/src/lib.rs` — 注册 filter 模块

```rust
mod filter;
```

---

## 单元测试

```rust
// ═══════════════════════════════════════════════════════════════
// MessageFilter — 类型过滤 (3 tests)
// ═══════════════════════════════════════════════════════════════

// [过滤] types=None → 全收（Trace 行为）
// [过滤] types=Some([...]) → 匹配的通过，不匹配的跳过
// [过滤] types=Some([]) → 空白名单，全部拒绝

// ═══════════════════════════════════════════════════════════════
// MessageFilter — ToMatch 过滤 (4 tests)
// ═══════════════════════════════════════════════════════════════

// [过滤] ToMatch::All → 定向和广播都收到
// [过滤] ToMatch::BroadcastOnly → 只收广播
// [过滤] ToMatch::DirectedToMe → 只收定向到自己的
// [过滤] ToMatch::BroadcastAndDirectedToMe → 广播+定向到自己的都收

// ═══════════════════════════════════════════════════════════════
// MessageFilter — 组合 (1 test)
// ═══════════════════════════════════════════════════════════════

// [过滤] type+ToMatch 组合：只收特定 type 的广播
```

### 测试代码（在 `filter.rs` 的 `#[cfg(test)]`）

```rust
// [过滤] types=None → 全收
#[test]
fn filter_types_none_accepts_all() {
    let filter = MessageFilter { types: None, to_match: ToMatch::All };
    let msg = Message::new("any", NodeId::new("a"), vec![], serde_json::json!(null));
    assert!(filter.matches(&msg, &NodeId::new("me")));
}

// [过滤] type 匹配 → 通过
#[test]
fn filter_type_match_passes() {
    let filter = MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::All,
    };
    let msg = Message::new("action", NodeId::new("a"), vec![], serde_json::json!(null));
    assert!(filter.matches(&msg, &NodeId::new("me")));
}

// [过滤] type 不匹配 → 拒绝
#[test]
fn filter_type_mismatch_rejects() {
    let filter = MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::All,
    };
    let msg = Message::new("other", NodeId::new("a"), vec![], serde_json::json!(null));
    assert!(!filter.matches(&msg, &NodeId::new("me")));
}

// [过滤] types=Some([]) → 全部拒绝
#[test]
fn filter_empty_type_list_rejects_all() {
    let filter = MessageFilter {
        types: Some(vec![]),
        to_match: ToMatch::All,
    };
    let msg = Message::new("any", NodeId::new("a"), vec![], serde_json::json!(null));
    assert!(!filter.matches(&msg, &NodeId::new("me")));
}

// [过滤] ToMatch::All → 广播和定向都收
#[test]
fn to_match_all_receives_both() {
    let filter = MessageFilter { types: None, to_match: ToMatch::All };
    let me = NodeId::new("me");
    assert!(filter.matches(&Message::new("t", NodeId::new("a"), vec![], serde_json::json!(null)), &me));
    assert!(filter.matches(&Message::new("t", NodeId::new("a"), vec![me.clone()], serde_json::json!(null)), &me));
}

// [过滤] ToMatch::BroadcastOnly → 只收广播
#[test]
fn to_match_broadcast_only() {
    let filter = MessageFilter { types: None, to_match: ToMatch::BroadcastOnly };
    let me = NodeId::new("me");
    assert!(filter.matches(&Message::new("t", NodeId::new("a"), vec![], serde_json::json!(null)), &me));
    assert!(!filter.matches(&Message::new("t", NodeId::new("a"), vec![me.clone()], serde_json::json!(null)), &me));
}

// [过滤] ToMatch::DirectedToMe → 只收定向到自己的
#[test]
fn to_match_directed_to_me() {
    let filter = MessageFilter { types: None, to_match: ToMatch::DirectedToMe };
    let me = NodeId::new("me");
    assert!(filter.matches(&Message::new("t", NodeId::new("a"), vec![me.clone()], serde_json::json!(null)), &me));
    assert!(!filter.matches(&Message::new("t", NodeId::new("a"), vec![], serde_json::json!(null)), &me));
    assert!(!filter.matches(&Message::new("t", NodeId::new("a"), vec![NodeId::new("other")], serde_json::json!(null)), &me));
}

// [过滤] ToMatch::BroadcastAndDirectedToMe
#[test]
fn to_match_broadcast_and_directed() {
    let filter = MessageFilter { types: None, to_match: ToMatch::BroadcastAndDirectedToMe };
    let me = NodeId::new("me");
    assert!(filter.matches(&Message::new("t", NodeId::new("a"), vec![], serde_json::json!(null)), &me));
    assert!(filter.matches(&Message::new("t", NodeId::new("a"), vec![me.clone()], serde_json::json!(null)), &me));
    assert!(!filter.matches(&Message::new("t", NodeId::new("a"), vec![NodeId::new("other")], serde_json::json!(null)), &me));
}

// [过滤] type+ToMatch 组合
#[test]
fn filter_type_and_to_match_combined() {
    let filter = MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::BroadcastOnly,
    };
    let me = NodeId::new("me");
    // Correct type + broadcast → pass
    assert!(filter.matches(&Message::new("action", NodeId::new("a"), vec![], serde_json::json!(null)), &me));
    // Wrong type + broadcast → reject
    assert!(!filter.matches(&Message::new("other", NodeId::new("a"), vec![], serde_json::json!(null)), &me));
    // Correct type + directed → reject
    assert!(!filter.matches(&Message::new("action", NodeId::new("a"), vec![me.clone()], serde_json::json!(null)), &me));
}
```

### 集成测试：端到端 recv() 过滤

```rust
// In connection.rs tests:

// [过滤] recv() 只返回通过 filter 的消息
#[tokio::test]
async fn recv_respects_message_filter() {
    let bus = test_bus();
    // receiver only wants "action" type broadcasts
    let filter = MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::BroadcastOnly,
    };
    let mut receiver = bus.connect(test_node_info("r"), filter).await.unwrap();
    let sender = bus.connect(test_node_info("s"), test_filter()).await.unwrap();

    // Drain sender's node_online (it passes through receiver's filter?
    // node_online type is not in ["action"] → filtered out)
    // Send a non-matching message
    sender.send("noise", vec![], serde_json::json!(null)).await.unwrap();
    // Send a matching message
    sender.send("action", vec![], serde_json::json!("run")).await.unwrap();

    // Should receive "action" (and skip node_online + "noise")
    let msg = receiver.recv().await.unwrap();
    assert_eq!(msg.msg_type, "action");
    assert_eq!(msg.payload, serde_json::json!("run"));

    receiver.disconnect().await;
    sender.disconnect().await;
    bus.shutdown().await;
}
```

---

## 测试清单

| # | 位置 | 角度 | 测试名 | 覆盖 |
|---|------|------|--------|------|
| 1 | filter.rs | `[过滤]` | `filter_types_none_accepts_all` | types=None 全收 |
| 2 | filter.rs | `[过滤]` | `filter_type_match_passes` | type 匹配通过 |
| 3 | filter.rs | `[过滤]` | `filter_type_mismatch_rejects` | type 不匹配拒 |
| 4 | filter.rs | `[过滤]` | `filter_empty_type_list_rejects_all` | 空白名单全拒 |
| 5 | filter.rs | `[过滤]` | `to_match_all_receives_both` | All 双向通过 |
| 6 | filter.rs | `[过滤]` | `to_match_broadcast_only` | BroadcastOnly |
| 7 | filter.rs | `[过滤]` | `to_match_directed_to_me` | DirectedToMe |
| 8 | filter.rs | `[过滤]` | `to_match_broadcast_and_directed` | BroadcastAndDirectedToMe |
| 9 | filter.rs | `[过滤]` | `filter_type_and_to_match_combined` | 组合过滤 |
| 10 | connection.rs | `[过滤]` | `recv_respects_message_filter` | recv 端到端 |

---

## 对已有测试的影响

- 已有测试全部使用 `test_filter()` (= `types: None, to_match: ToMatch::All`) → 行为不变
- `recv_receives_messages_in_order` 等测试——filter 为 All，不受影响
- heartbeat 相关测试——heartbeat 在 filter 之前已拦截，不受影响

---

## 小结

- **MessageFilter::matches()** 在 `filter.rs` 实现，独立的纯函数，易于单测
- **recv()/try_recv()** 在 heartbeat 过滤后增加 filter 检查
- **10 个新测试**：8 个 filter 单元测试 + 1 个组合测试 + 1 个 E2E recv 测试
