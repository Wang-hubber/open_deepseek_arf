# 任务 1.9：集成测试

> Phase 1 — Bus 消息总线第九项任务
> 父文档：`docs/v1.x/phase1-bus-design.md`
> 前置：任务 1.2-1.7 全部完成

## 设计思路

集成测试验证多组件协作场景——多个 NodeHandle + Bus + 心跳 + 过滤在真实 tokio runtime 下的行为。放在 `crates/arf-bus/tests/integration.rs`（独立于 `src/` 的集成测试目录）。

## 测试场景

### 基础多节点 (6 tests)

| # | 场景 | 描述 |
|---|------|------|
| 1 | 上线/下线广播 | 3 节点互相看到 node_online/node_offline |
| 2 | 定向消息过滤 | DirectedToMe 只收定向到己，BroadcastOnly 只收广播 |
| 3 | 心跳超时 | 僵尸节点停止消费 → 超时 → node_offline 被其他节点看到 |
| 4 | Trace 全量消费 | ToMatch::All + types=None → 看到所有消息 |
| 5 | Lag 恢复 | 慢消费者 Lagged → 继续正常接收 |
| 6 | 并发 connect/disconnect/send | 不丢消息不 panic |

### Corner case (6 tests)

| # | 场景 | 为什么是 corner | 描述 |
|---|------|----------------|------|
| 7 | **Late joiner + graph()** | 新节点 subscribe 晚于已有节点的 node_online，看不到历史 | 新节点通过 `graph()` 获取当前在线节点列表，补偿消息流的"不可见窗口" |
| 8 | **多 filter 不同子集** | 同一批消息经过不同 filter，各节点收到不同子集 | 3 节点：All+全type、DirectedToMe+action、BroadcastOnly+action → 发广播 action + 定向 action + 广播 noise → 验证各自收到正确的子集 |
| 9 | **心跳超时 + 同 NodeId 重连** | 僵尸超时下线后同 ID 重新连接，其他节点看到完整周期 | node_offline(zombie) → node_online(zombie) 顺序正确，新 handle 正常收发 |
| 10 | **disconnect 时消息还在广播缓冲里** | send 和 disconnect 在消息循环中串行，但消息广播到所有 rx | 节点 A send 消息后立即 disconnect，节点 B 仍能收到该消息（消息先广播） |
| 11 | **快速 connect/disconnect 循环** | 资源泄漏风险 | 同一 NodeId connect → disconnect × 10 次，graph 始终正确，无残留 |
| 12 | **shutdown 时还有在线节点** | 优雅关闭 | Bus shutdown → 所有 NodeHandle.recv() 返回 Closed，graph 仍可读 |

---

## 代码结构

```rust
// crates/arf-bus/tests/integration.rs

use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch, SendError};
use std::sync::Arc;
use std::time::Duration;

fn node(id: &str, node_type: &str) -> NodeInfo { ... }
fn all_filter() -> MessageFilter { ... }
fn directed_filter() -> MessageFilter { ... }
fn broadcast_filter() -> MessageFilter { ... }
fn action_only_filter() -> MessageFilter { ... }     // types=["action"], All
fn action_directed_filter() -> MessageFilter { ... }  // types=["action"], DirectedToMe
fn empty_filter() -> MessageFilter { ... }            // types=[], All
```

### 测试 8 详细：多 filter 不同子集

```
节点:
  trace:   All + types=None        → 应收到全部 4 条
  worker:  DirectedToMe + action   → 只收定向 action 1 条
  watcher: BroadcastOnly + action  → 只收广播 action 1 条

发送:
  1. sender.send("action", [])         → trace:✅  watcher:✅  worker:❌
  2. sender.send("action", [worker])   → trace:✅  watcher:❌  worker:✅
  3. sender.send("noise", [])          → trace:✅  watcher:❌  worker:❌
  4. sender.send("action", [watcher])  → trace:✅  watcher:❌  worker:❌
                                        (定向到 watcher 但 watcher 只要广播)
```

### 测试 9 详细：心跳超时 + 重连

```
1. zombie 连接，正常节点 observer 连接
2. observer drain zombie 的 node_online
3. zombie 停止 recv → 超时 → observer 收到 zombie 的 node_offline
4. zombie2 用同 NodeId 重连 → observer 收到 zombie 的 node_online
5. zombie2 正常收发
```

### 测试 10 详细：disconnect 时消息还在缓冲区

```
1. A, B 连接
2. A send 消息 → 消息进入 broadcast ring buffer
3. A disconnect → 消息循环先处理 send（广播），再处理 disconnect（node_offline）
4. B 仍然收到 A 的消息（先于 node_offline）
```

消息循环串行保证：cmd 队列中 send 在 disconnect 前面 → 先广播消息，再执行 disconnect。

---

## 小结

- **12 个集成测试**：6 基础 + 6 corner
- 覆盖时序、过滤、生命周期、资源四个维度的边界
- 不与单元测试重复（单元测单组件，集成测多组件协作）
