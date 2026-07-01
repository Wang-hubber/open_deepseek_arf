# 任务 1.6：接收侧过滤

> Phase 1 — Bus 消息总线第六项任务
> 父文档：`docs/v1.x/phase1/phase1-bus-design.md`
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

### 覆盖矩阵：`to_match × to`

`to` 现在是 `Vec<NodeId>`，多目标组合需要系统覆盖：

| to_match | to=[] | to=[me] | to=[other] | to=[me,other] | to=[o1,o2] |
|----------|-------|---------|------------|---------------|------------|
| All | pass | pass | pass | pass | pass |
| BroadcastOnly | pass | **reject** | **reject** | **reject** | **reject** |
| DirectedToMe | **reject** | pass | **reject** | pass | **reject** |
| BroadcastAndDirectedToMe | pass | pass | **reject** | pass | **reject** |

- `to=[]` = 广播
- `to=[me]` = 定向到自己
- `to=[other]` = 定向到别人
- `to=[me, other]` = 多目标含自己
- `to=[o1, o2]` = 多目标不含自己

### `types` 维度

| types | 覆盖 |
|-------|------|
| `None` | 全收 |
| `Some(["action"])` | 单类型匹配/不匹配 |
| `Some(["action", "query"])` | **多类型白名单，任一匹配即通过** |
| `Some([])` | 空白名单全拒 |

### 组合维度（type × to_match）

| 场景 | 覆盖 |
|------|------|
| type ✅ to_match ✅ | pass |
| type ✅ to_match ❌ | **reject（type 对但目标不对）** |
| type ❌ to_match ✅ | **reject（目标对但 type 不对）** |
| type ❌ to_match ❌ | reject |

---

### 测试清单

| # | 位置 | 角度 | 测试名 | 覆盖 |
|---|------|------|--------|------|
| 1 | filter.rs | `[过滤]` | `filter_types_none_accepts_all` | types=None 全收 |
| 2 | filter.rs | `[过滤]` | `filter_type_match_passes` | 单 type 匹配 |
| 3 | filter.rs | `[过滤]` | `filter_type_mismatch_rejects` | 单 type 不匹配 |
| 4 | filter.rs | `[过滤]` | `filter_multi_type_any_match_passes` | **多 type 任一匹配** |
| 5 | filter.rs | `[过滤]` | `filter_empty_type_list_rejects_all` | 空白名单全拒 |
| 6 | filter.rs | `[过滤]` | `to_match_all_receives_everything` | All 全过 |
| 7 | filter.rs | `[过滤]` | `to_match_broadcast_only` | BroadcastOnly 矩阵 |
| 8 | filter.rs | `[过滤]` | `to_match_directed_to_me` | DirectedToMe 矩阵 |
| 9 | filter.rs | `[过滤]` | `to_match_broadcast_and_directed` | BroadcastAndDirectedToMe 矩阵 |
| 10 | filter.rs | `[过滤]` | `filter_type_pass_to_match_reject` | **type✅ to_match❌** |
| 11 | filter.rs | `[过滤]` | `filter_type_reject_to_match_pass` | **type❌ to_match✅** |
| 12 | filter.rs | `[过滤]` | `filter_both_reject` | 双 reject |
| 13 | connection.rs | `[过滤]` | `recv_respects_message_filter` | recv 端到端 |

### 测试代码（`filter.rs`）

```rust
// Helper
fn msg_to(to: Vec<NodeId>) -> Message {
    Message::new("action", NodeId::new("sender"), to, serde_json::json!(null))
}

// ═══════════════════════════════════════════════════════════════
// types 过滤 (5 tests)
// ═══════════════════════════════════════════════════════════════

// [过滤] types=None → 全收
#[test]
fn filter_types_none_accepts_all() {
    let filter = MessageFilter { types: None, to_match: ToMatch::All };
    let me = NodeId::new("me");
    assert!(filter.matches(&msg_to(vec![]), &me));
    assert!(filter.matches(&msg_to(vec![me.clone()]), &me));
    assert!(filter.matches(&msg_to(vec![NodeId::new("other")]), &me));
}

// [过滤] type 匹配 → 通过
#[test]
fn filter_type_match_passes() {
    let filter = MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::All,
    };
    assert!(filter.matches(&msg_to(vec![]), &NodeId::new("me")));
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

// [过滤] 多 type 白名单：任一匹配即通过
#[test]
fn filter_multi_type_any_match_passes() {
    let filter = MessageFilter {
        types: Some(vec!["action".into(), "query".into(), "response".into()]),
        to_match: ToMatch::All,
    };
    let me = NodeId::new("me");
    assert!(filter.matches(&Message::new("action", NodeId::new("a"), vec![], serde_json::json!(null)), &me));
    assert!(filter.matches(&Message::new("query", NodeId::new("a"), vec![], serde_json::json!(null)), &me));
    assert!(filter.matches(&Message::new("response", NodeId::new("a"), vec![], serde_json::json!(null)), &me));
    assert!(!filter.matches(&Message::new("noise", NodeId::new("a"), vec![], serde_json::json!(null)), &me));
}

// [过滤] types=Some([]) → 空白名单全拒
#[test]
fn filter_empty_type_list_rejects_all() {
    let filter = MessageFilter {
        types: Some(vec![]),
        to_match: ToMatch::All,
    };
    assert!(!filter.matches(&msg_to(vec![]), &NodeId::new("me")));
}

// ═══════════════════════════════════════════════════════════════
// ToMatch 过滤 (3 tests, 每个覆盖完整矩阵)
// ═══════════════════════════════════════════════════════════════

// [过滤] All — 所有消息通过
#[test]
fn to_match_all_receives_everything() {
    let filter = MessageFilter { types: None, to_match: ToMatch::All };
    let me = NodeId::new("me");
    let other = NodeId::new("other");
    assert!(filter.matches(&msg_to(vec![]), &me));               // broadcast
    assert!(filter.matches(&msg_to(vec![me.clone()]), &me));      // to=[me]
    assert!(filter.matches(&msg_to(vec![other.clone()]), &me));   // to=[other]
    assert!(filter.matches(&msg_to(vec![me.clone(), other.clone()]), &me)); // to=[me,other]
    assert!(filter.matches(&msg_to(vec![other.clone(), NodeId::new("x")]), &me)); // to=[o1,o2]
}

// [过滤] BroadcastOnly — 只收广播
#[test]
fn to_match_broadcast_only() {
    let filter = MessageFilter { types: None, to_match: ToMatch::BroadcastOnly };
    let me = NodeId::new("me");
    let other = NodeId::new("other");
    assert!(filter.matches(&msg_to(vec![]), &me));                         // broadcast → pass
    assert!(!filter.matches(&msg_to(vec![me.clone()]), &me));              // to=[me] → reject（定向不是广播）
    assert!(!filter.matches(&msg_to(vec![other.clone()]), &me));           // to=[other]
    assert!(!filter.matches(&msg_to(vec![me.clone(), other.clone()]), &me)); // to=[me,other]
    assert!(!filter.matches(&msg_to(vec![other.clone(), NodeId::new("x")]), &me)); // to=[o1,o2]
}

// [过滤] DirectedToMe — 只收定向到自己的
#[test]
fn to_match_directed_to_me() {
    let filter = MessageFilter { types: None, to_match: ToMatch::DirectedToMe };
    let me = NodeId::new("me");
    let other = NodeId::new("other");
    assert!(!filter.matches(&msg_to(vec![]), &me));                        // broadcast → reject
    assert!(filter.matches(&msg_to(vec![me.clone()]), &me));               // to=[me] → pass
    assert!(!filter.matches(&msg_to(vec![other.clone()]), &me));           // to=[other] → reject
    assert!(filter.matches(&msg_to(vec![me.clone(), other.clone()]), &me)); // to=[me,other] → pass
    assert!(!filter.matches(&msg_to(vec![other.clone(), NodeId::new("x")]), &me)); // to=[o1,o2] → reject
}

// [过滤] BroadcastAndDirectedToMe — 广播 + 定向到自己的
#[test]
fn to_match_broadcast_and_directed() {
    let filter = MessageFilter { types: None, to_match: ToMatch::BroadcastAndDirectedToMe };
    let me = NodeId::new("me");
    let other = NodeId::new("other");
    assert!(filter.matches(&msg_to(vec![]), &me));                         // broadcast → pass
    assert!(filter.matches(&msg_to(vec![me.clone()]), &me));               // to=[me] → pass
    assert!(!filter.matches(&msg_to(vec![other.clone()]), &me));           // to=[other] → reject
    assert!(filter.matches(&msg_to(vec![me.clone(), other.clone()]), &me)); // to=[me,other] → pass
    assert!(!filter.matches(&msg_to(vec![other.clone(), NodeId::new("x")]), &me)); // to=[o1,o2] → reject
}

// ═══════════════════════════════════════════════════════════════
// type + ToMatch 组合 (3 tests)
// ═══════════════════════════════════════════════════════════════

// [过滤] type ✅ to_match ❌ → 拒绝
#[test]
fn filter_type_pass_to_match_reject() {
    let filter = MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::DirectedToMe,
    };
    let me = NodeId::new("me");
    let action_broadcast = Message::new("action", NodeId::new("a"), vec![], serde_json::json!(null));
    // type "action" matches, but broadcast doesn't pass DirectedToMe
    assert!(!filter.matches(&action_broadcast, &me));
}

// [过滤] type ❌ to_match ✅ → 拒绝
#[test]
fn filter_type_reject_to_match_pass() {
    let filter = MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::All,
    };
    let me = NodeId::new("me");
    let noise_to_me = Message::new("noise", NodeId::new("a"), vec![me.clone()], serde_json::json!(null));
    // to_match passes (All), but "noise" type doesn't match
    assert!(!filter.matches(&noise_to_me, &me));
}

// [过滤] type ❌ to_match ❌ → 双拒绝
#[test]
fn filter_both_reject() {
    let filter = MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::DirectedToMe,
    };
    let me = NodeId::new("me");
    let noise_broadcast = Message::new("noise", NodeId::new("a"), vec![], serde_json::json!(null));
    assert!(!filter.matches(&noise_broadcast, &me));
}
```

### 集成测试（`connection.rs`）

```rust
// [过滤] recv() 只返回通过 filter 的消息
#[tokio::test]
async fn recv_respects_message_filter() {
    let bus = test_bus();
    let filter = MessageFilter {
        types: Some(vec!["action".into()]),
        to_match: ToMatch::BroadcastOnly,
    };
    let mut receiver = bus.connect(test_node_info("r"), filter).await.unwrap();
    let sender = bus.connect(test_node_info("s"), test_filter()).await.unwrap();
    // sender's node_online has type "node_online" → filtered out by types
    // Send non-matching type → filtered
    sender.send("noise", vec![], serde_json::json!(null)).await.unwrap();
    // Send non-matching to_match → filtered
    sender.send("action", vec![NodeId::new("r")], serde_json::json!("directed")).await.unwrap();
    // Send matching (action + broadcast) → should pass
    sender.send("action", vec![], serde_json::json!("run")).await.unwrap();

    let msg = receiver.recv().await.unwrap();
    assert_eq!(msg.msg_type, "action");
    assert_eq!(msg.payload, serde_json::json!("run"));

    receiver.disconnect().await;
    sender.disconnect().await;
    bus.shutdown().await;
}
```

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
