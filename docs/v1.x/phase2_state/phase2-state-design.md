# Phase 2 — State 设计

> 父文档：`docs/v1.x/v1.x-design.md` §4、`docs/v1.x/2026-06-26-arfv1-roadmap.md`
> 依赖：Phase 1 (Bus) — 已完成

## 定位

State 是**纯数据结构**，定义在 `arf-state` crate。不包含任何方法——Engine (Phase 3) 持有 State 实例并负责所有操作（状态转换、级联传播、持久化时机）。

## 领域边界

| 概念 | 管理位置 | 职责 |
|------|---------|------|
| `messages` | State | 模型对话历史（经过 ModelAdapter 整理），Engine 恢复上下文用 |
| `tasks` | State | A2A 任务，有生命周期 + 双向锁 + 级联释放 |
| ReAct 循环消息（model_call/tool_call） | Bus | 在 Bus 上实时流动，不在 State 里存 |

`messages` 和 `tasks` 职责分开——前者管 ReAct 上下文恢复，后者管 A2A 协调。

## 类型设计

### `arf-core` 新增

```rust
// TaskId — A2A 任务标识
// UUID 不承载业务信息，owner 字段标识创建者
pub struct TaskId {
    pub id: Uuid,
    pub owner: NodeId,
}

// TaskStatus — 6 状态
pub enum TaskStatus {
    Created,
    InProgress,
    Blocked,
    Resolved,    // 终态
    Failed,      // 终态
    Cancelled,   // 终态
}

// ModelMessage — 最小对话消息，Phase 5 扩展
pub struct ModelMessage {
    pub role: String,     // "user" | "assistant" | "system" | "tool"
    pub content: String,
}
```

### 状态转换图

```
created ──→ in_progress ──→ resolved
    │            │  ↑          failed
    │            │  │ 唤醒     cancelled
    │            │  │
    └──→ blocked ──┘
              │
              ├──→ cancelled (级联取消)
              └──→ failed    (节点离线)
```

Engine 负责合法转换校验。终态不可逆。

### `arf-state` 实现

```rust
pub struct Task {
    pub id: TaskId,
    pub status: TaskStatus,
    /// 本地 task → 依赖的外部 task 列表
    /// 如 taskA_1 blocked_by: {taskA_1: [taskB_1, taskB_2]}
    pub blocked_by: HashMap<TaskId, Vec<TaskId>>,
    /// 本地 task → 被哪些外部 task 依赖
    /// B 侧: taskB_1 blocking: {taskB_1: [taskA_1]}
    pub blocking: HashMap<TaskId, Vec<TaskId>>,
    pub metadata: serde_json::Value,
}

pub struct State {
    pub messages: Vec<ModelMessage>,
    pub tasks: Vec<Task>,
}
```

双向锁分两半存：`blocked_by` 和 `blocking` 各存一个方向。两边合起来可还原完整依赖链。锁同时存在 A 和 B 两侧——A 侧声明"我等谁"，B 侧声明"谁在等我"。

## 与后续 Phase 的关系

| Phase | 如何使用 State |
|-------|---------------|
| Phase 3 Engine | 持有 `State`，负责创建 task、状态转换、级联传播、持久化读写 |
| Phase 5 ModelAdapter | 扩展 `ModelMessage` 字段（如 `tool_calls`、`tool_call_id` 等） |

## 任务拆解

| # | 任务 | 内容 | 产出 |
|---|------|------|------|
| 2.1 | `arf-core` 新增类型 | `TaskId` + `TaskStatus` + `ModelMessage` 定义 + trait impl (Debug, Clone, Eq, Hash, Serialize, Deserialize) | `crates/arf-core/src/lib.rs` |
| 2.2 | `arf-state` 实现 | `Task` + `State` 结构体 + serde | `crates/arf-state/src/lib.rs` |
| 2.3 | 单元测试 | 构造、序列化往返、合法状态、边界 | 各模块 `#[cfg(test)]` |
| 2.4 | 文档 | Phase 2 设计文档 | `docs/v1.x/phase2/` |

---

## 交付标准

- `cargo test --workspace` 全部通过
- `cargo fmt --check` + `cargo clippy` 无警告
- TaskId/ModelMessage/State 可序列化往返
- `arf-state` 不使用任何 ARF Bus 依赖
