# 任务 6.0.5：§9.A 收尾 + 文档

> Phase 6 — Multi-Bus 基础设施（§9.A）最后一项任务
> 前置：`task-6.0.1` ✅ / `task-6.0.2` ✅ / `task-6.0.3` ✅ / `task-6.0.4` ✅

## 目标

收尾 §9.A，把任务实施期间发现的设计/实现差异整理记录，并补全最后一组集成测试。

## 关键发现汇总（对照设计文档 §7）

| §7.1 / §7.2 设计增量 | 实际 | 差异 |
|--------------------|------|------|
| `ActionMessage` trait (`§7.1`) | 未实现（推迟到 §9.B 6.1） | 按计划 |
| `MessageIntent` enum | 未实现 | 按计划 |
| `Node` trait (`§7.1`) | ✅ 6.0.1 实现 | 与设计吻合 |
| `Checkpoint` / `CheckpointRule` | 未实现 | 推迟到 6.1 |
| `Capability` / `Route` / `State` / `OverView` | 未实现 | 推迟到 6.1 |
| `Response` enum | 未实现 | 推迟到 6.2 |
| `WaitEvent` / `WaitStrategy` | 未实现 | 推迟到 6.6 |
| `FailedReason` / `OnMemberFailedHandler` | 未实现 | 推迟到 6.8 |
| `BuildError` 等错误 | 部分实现（仅 `SendError::NoSuchBus`、`SnapshotError`、`RestoreError`） | 其他推迟 |
| **`Message.from_bus: Option<BusId>` (§7.2)** | ✅ 6.0.3 实现 | 关键差异：`#[serde(default)]` 兼容 Phase 1 历史 JSON |
| **`NodeHandle::attach_to(bus, filter)` (§7.2)** | ✅ 6.0.2 实现 | 关键差异：forwarding task 每订阅一个 |
| **每 sub 独立 broadcast_rx 在 forwarding task 里** | ✅ | — |
| `NodeHandle::send_via(bus, ...)` | ✅ 6.0.2/6.0.3 实现 | — |
| **`Bus::barrier()`** | ✅ 6.0.4 实现 | 比设计更复杂（acks by correlation_id，需 participants 列表过滤） |
| `Bus::id: BusId` | ✅ 6.0.2 (提早) / 6.0.3 (stamping) | — |
| `BusGraph` 调整 | 未动 | 不在 §9.A 范围 |

### 偏离设计的语义变更（必读）

| 变更 | 设计原文 | 实际 | 影响 |
|------|---------|------|------|
| **心跳协议** | "NodeHandle 不调 recv → 不 ack → Bus 超时清理" | "NodeHandle 持有期间始终 ack；drop 后立即停 ack"（forwarding task 拓扑） | 用 drop 模拟掉线，而非 idle |
| **Lagged 暴露** | NodeHandle::recv() 可暴露 `RecvError::Lagged` | forwarding task 在内部吞 Lagged；NodeHandle.recv() 永不返回 Lagged | 上层 API 不再强调 Lagged |
| **mpsc vs broadcast 入站** | 设计只说"过滤" | 每订阅一个独立 inbound mpsc（容量 16），NodeHandle 用 `futures::future::select_all` 读 | 内存换简单，无 broadcast Lagged 干扰 |

## 最终集成测试

`crates/arf-bus/tests/integration.rs` 新增 2 个 §9.A 收尾测试：

```rust
// [§9.A 收尾] Bus::barrier 在多订阅 NodeHandle 上正确工作
#[tokio::test]
async fn barrier_with_node_handle_ack_via_attach() {
    // facade Node 模式：handle attach 到 2 个 Bus，对每个 Bus 都 ack
    // barrier 调用同时等 2 个 Bus 上的 snapshot 完成
    // (测试 §2.P9 facade + §2.P7 multi-Bus 交叉场景)
}

// [§9.A 收尾] NodeHandle 在多 Bus 上保持稳定：subscribe → send_via → recv 综合
#[tokio::test]
async fn node_handle_full_lifecycle_multi_bus() {
    // 1 个 handle 连 bus_a (primary) + bus_b (attach)
    // bus_a 收 model_call，bus_b 收 tool_exec
    // 用 send_via 分别投递；用 recv() 收两路消息
}
```

## 自审文档清理

`docs/v1.x/phase6/self_review.md` 的 C1（Rust `name="x"` 命名参数语法错误）仍未修复。三处：

| 位置 | 行号 |
|------|------|
| §3 App 装配示例 | 557–571 |
| §11.2 Multi-model 投票 | 1070–1074 |
| §11.3 上下文压缩 | 1083–1090 |

按 self_review.md 的修复方向，三处都改成 struct literal（与 `CheckpointRule::new` 的位置参数签名匹配）。

修复（commit `docs(engine):`）一并合入 6.0.5。这影响设计文档的可读性，与代码无关。

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test --workspace
```

## 测试覆盖摘要

| 模块 | 测试数 | 备注 |
|------|--------|------|
| `arf-core` lib | 134 | 含 6.0.1（Node）+ 6.0.3（from_bus）共 51 个新增 |
| `arf-bus` lib | 91 | 含 6.0.2 + 6.0.4 共 13 个新增 |
| `arf-bus` integration | 12 | 1 个 6.0.5 新增（facade barrier） |
| **合计 arf-bus 相关** | **237** | 含本任务实施期全部测试 |

---

## 实现后实际发现

> 以下章节在实施完成后回填。

### 6.0.5 实际实施

新增 2 个集成测试：
- `barrier_facade_with_attach_to_two_buses`：facade Node（subscribe 2 个 Bus）跨 Bus 响应 barrier。验证多 Bus + barrier 组合的正确性
- `node_handle_full_lifecycle_two_buses`：NodeHandle 完整 lifecycle：connect → attach_to → send_via → recv

C1（设计文档中 `CheckpointRule::new(name="...", trigger=..., ...)` Rust 命名参数语法错误）已修复为 struct literal（与 §1.5 中 `CheckpointRule { name, trigger, when, build }` 字段签名匹配）。

注：self_review.md §2753 行声称 §11.2 / §11.3 也有 Rust 命名参数问题——实际这两段是 Python 代码（kwargs 在 Python 合法），无需修复。

### §9.A 完成状态

| Task | Commit | 主要内容 | 测试增量 |
|------|--------|---------|---------|
| 6.0.1 | ddbfb62 / 04d1e98 | Node trait + BusId | +46 |
| 6.0.2 | 1e5add6 | NodeHandle 多 Bus 订阅 + forwarding task | -3 +9 (净 +6) |
| 6.0.3 | 45e4239 | Message.from_bus + BusId stamping | +5 +3 |
| 6.0.4 | d68df31 | Bus::barrier() 原语 | +6 |
| 6.0.5 | (本次) | §9.A 收尾 + C1 doc 修复 | +2 集成 |

### 测试状态（最终）

```
cargo test --workspace
...
test result: ok. 91  passed  (arf-bus lib)
test result: ok. 14  passed  (arf-bus integration, was 12 → +2)
test result: ok. 134 passed  (arf-core)
test result: ok. 204 passed  (其他)
test result: ok. 70  passed  (其他)
... (其他 crate 全部 OK)
0 FAILED
```

### 进入 §9.B 前的待办

§9.B 任务（6.1 ~ 6.10）依赖 §9.A 完成。现在的"地基"已稳：
- Node trait + 错误类型
- 多 Bus 订阅 + 接收端过滤
- from_bus 戳 + 序列化兼容
- barrier 协调原语

下一步：进入 6.1（核心类型：ActionMessage / Route / Capability / State / OverView / Checkpoint / CheckpointRule）。