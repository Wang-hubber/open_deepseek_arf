# 任务 1.9：集成测试

> Phase 1 — Bus 消息总线第九项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.2-1.7 全部完成

## 设计思路

集成测试验证多组件协作场景——多个 NodeHandle + Bus + 心跳 + 过滤在真实 tokio runtime 下的行为。放在 `crates/arf-bus/tests/integration.rs`（独立于 `src/` 的集成测试目录）。

## 测试场景

| # | 场景 | 描述 |
|---|------|------|
| 1 | 上线/下线广播 | 3 节点互相看到 node_online/node_offline |
| 2 | 定向消息过滤 | DirectedToMe 节点只收定向到自己的，BroadcastOnly 只收广播 |
| 3 | 心跳超时 | 僵尸节点停止消费 → 超时 → node_offline 被其他节点看到 |
| 4 | Trace 全量消费 | ToMatch::All + types=None → 看到所有消息 |
| 5 | Lag 恢复 | 慢消费者 Lagged → 继续正常接收 |
| 6 | 并发 connect/disconnect/send | 多节点并发操作不丢消息不 panic |

每个场景是一个 `#[tokio::test]`。

---

## 代码结构

```rust
// crates/arf-bus/tests/integration.rs

use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use std::time::Duration;

fn node(id: &str, node_type: &str) -> NodeInfo { ... }
fn all_filter() -> MessageFilter { ... }
fn directed_filter() -> MessageFilter { ... }
fn broadcast_filter() -> MessageFilter { ... }
fn action_filter() -> MessageFilter { ... }
```

---

## 小结

- 6 个集成测试场景
- 验证多节点在真实 tokio runtime 下的协作行为
- 不与任何单元测试重复（单元测试测单组件边界，集成测试测多组件协作）
