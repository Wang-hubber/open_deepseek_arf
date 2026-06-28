# 任务 2.2：`arf-state` 实现

> Phase 2 — State 第二项任务
> 父文档：`docs/v1.x/phase2_state/phase2-state-design.md`
> 依赖：2.1（`arf-core` 新增类型，已完成）

## 设计思路

`arf-state` 是纯数据结构 crate，定义 `Task` 和 `State`，不包含业务方法。所有操作（状态转换、级联传播、持久化）由 Phase 4 Engine 负责。

| 结构体 | 用途 | 字段 |
|--------|------|------|
| `Task` | A2A 任务，含双向锁信息 | id, status, blocked_by, blocking, metadata |
| `State` | Agent 持有状态 | messages (对话历史), tasks (任务列表) |

`Task` 的双向锁分两半存储：

```
Agent A 的 task_a1 等待 Agent B 的 task_b1, task_b2

A 侧: task_a1.blocked_by = {task_a1: [task_b1, task_b2]}
B 侧: task_b1.blocking  = {task_b1: [task_a1]}
       task_b2.blocking  = {task_b2: [task_a1]}
```

两边合起来可还原完整依赖链。锁同时存在于 A 和 B 两侧——A 侧声明"我等谁"，B 侧声明"谁在等我"。

---

## 代码实现

### `crates/arf-state/Cargo.toml`

当前只有 `arf-core` 依赖。`Task` 和 `State` 需要 `#[derive(Serialize, Deserialize)]`，需增加 `serde` + `serde_json`：

```toml
[package]
name = "arf-state"
version.workspace = true
edition.workspace = true
license.workspace = true
repository.workspace = true
description = "ARF State: messages + tasks with lifecycle, bidirectional locks, cascade release"

[dependencies]
arf-core = { path = "../arf-core" }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

逐行：
- `serde` — `#[derive(Serialize, Deserialize)]` 需要 `derive` feature
- `serde_json` — `metadata` 字段类型 `serde_json::Value` 所需
- 两个依赖 workspace 内版本统一，由根 `Cargo.toml` 管理

---

### `crates/arf-state/src/lib.rs`

完整替换当前占位内容：

```rust
//! ARF State — task lifecycle and message history.
//!
//! `Task` records A2A task state with bidirectional locking information.
//! `State` is the agent-held aggregate: conversation messages + task list.
//! This crate defines pure data structures — all operations (state transitions,
//! cascade propagation, persistence) belong to Phase 4 Engine.

use arf_core::{ModelMessage, TaskId, TaskStatus};
use serde::{Deserialize, Serialize};

// ── Task ────────────────────────────────────────────────────────────

/// An A2A task with lifecycle status and bidirectional locking.
///
/// Bidirectional locks are stored in two halves:
/// - `blocked_by`: "who I'm waiting for" (external tasks this task depends on)
/// - `blocking`: "who's waiting for me" (external tasks that depend on this task)
///
/// Together they reconstruct the full dependency graph across agents.
///
/// Note: `blocked_by` and `blocking` use `Vec<(TaskId, Vec<TaskId>)>`
/// rather than `HashMap` because JSON object keys must be strings and
/// `TaskId` is a compound type.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Task {
    pub id: TaskId,
    pub status: TaskStatus,
    /// Local task → list of external tasks it depends on.
    pub blocked_by: Vec<(TaskId, Vec<TaskId>)>,
    /// Local task → list of external tasks that depend on it.
    pub blocking: Vec<(TaskId, Vec<TaskId>)>,
    /// Arbitrary metadata attached to the task.
    pub metadata: serde_json::Value,
}
```

逐行：
- `use arf_core::{ModelMessage, TaskId, TaskStatus}` — 从 2.1 定义的共享类型导入，不重复定义
- `blocked_by: Vec<(TaskId, Vec<TaskId>)>` — 每项是 (本地 task, 依赖的外部 task 列表)。用 `Vec` 而非 `HashMap` 因为 JSON object key 必须为字符串，`TaskId` 是复合类型无法直接做 key
- `blocking: Vec<(TaskId, Vec<TaskId>)>` — 每项是 (本地 task, 依赖它的外部 task 列表)
- `metadata: serde_json::Value` — 任意 JSON，业务方自定义
- 不派生 `Hash`/`Eq` — `serde_json::Value` 含 `f64` 的 NaN 语义不满足 `Eq`，用 `PartialEq` 足够

```rust
impl Task {
    /// Create a new Task in `Created` status.
    pub fn new(id: TaskId) -> Self {
        Self {
            id,
            status: TaskStatus::Created,
            blocked_by: Vec::new(),
            blocking: Vec::new(),
            metadata: serde_json::Value::Null,
        }
    }

    /// Set the task metadata.
    pub fn with_metadata(mut self, metadata: serde_json::Value) -> Self {
        self.metadata = metadata;
        self
    }
}
```

逐行：
- `new(id)` — 创建任务，初始状态 `Created`，锁和元数据为空
- `with_metadata(value)` — builder 风格设置元数据
- 不提供 `with_status` 等修改方法 — 状态转换由 Engine 负责，不在数据结构层暴露

---

```rust
// ── State ────────────────────────────────────────────────────────────

/// Agent-held state: conversation messages + A2A task list.
///
/// `State` is the aggregate root for an agent session.
/// Phase 4 Engine owns the `State` instance and manages all mutations,
/// persistence, and cascade propagation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct State {
    /// Model conversation history. Used by Engine for context recovery.
    pub messages: Vec<ModelMessage>,
    /// Active A2A tasks with lifecycle status and locking info.
    pub tasks: Vec<Task>,
}
```

逐行：
- `messages: Vec<ModelMessage>` — 完整对话历史，Engine 从 State 恢复上下文传给 ModelAdapter
- `tasks: Vec<Task>` — 当前 session 的所有 A2A 任务，Engine 管理生命周期和级联传播
- 结构体只有字段，无方法 — 纯数据，Engine 全权操作

```rust
impl State {
    /// Create an empty State.
    pub fn new() -> Self {
        Self {
            messages: Vec::new(),
            tasks: Vec::new(),
        }
    }
}

impl Default for State {
    fn default() -> Self {
        Self::new()
    }
}
```

逐行：
- `new()` — 空 State，messages 和 tasks 均为空 Vec
- `impl Default` — 标准 Rust trait，方便框架中 `State::default()` 和 `#[derive(Default)]` 场景（未 derive 因为 `State` 当前结构简单可直接手写，且 `Default` 在 `serde_json::Value` 已实现）

---

## 测试

所有测试在 `crates/arf-state/src/lib.rs` 的 `#[cfg(test)] mod tests` 块内。

### Task — 8 tests

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use arf_core::NodeId;

    // ═══════════════════════════════════════════════════════════════
    // Task — 8 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] new() 创建的任务状态为 Created，锁和元数据为空
    #[test]
    fn task_new_creates_with_defaults() {
        let owner = NodeId::new("engine/main");
        let tid = TaskId::new(owner);
        let task = Task::new(tid.clone());
        assert_eq!(task.id, tid);
        assert_eq!(task.status, TaskStatus::Created);
        assert!(task.blocked_by.is_empty());
        assert!(task.blocking.is_empty());
        assert_eq!(task.metadata, serde_json::Value::Null);
    }

    // [构造] with_metadata() 设置元数据
    #[test]
    fn task_with_metadata() {
        let tid = TaskId::new(NodeId::new("engine/a"));
        let meta = serde_json::json!({"priority": 1, "description": "test task"});
        let task = Task::new(tid).with_metadata(meta.clone());
        assert_eq!(task.metadata, meta);
    }

    // [边界] blocked_by 为空 Vec：任务不依赖任何外部任务
    #[test]
    fn task_empty_blocked_by() {
        let task = Task::new(TaskId::new(NodeId::new("a")));
        assert!(task.blocked_by.is_empty());
    }

    // [边界] blocking 为空 Vec：没有外部任务依赖此任务
    #[test]
    fn task_empty_blocking() {
        let task = Task::new(TaskId::new(NodeId::new("a")));
        assert!(task.blocking.is_empty());
    }

    // [构造] blocked_by 有多个 entry：一个本地 task 依赖多个外部 task
    #[test]
    fn task_with_blocked_by() {
        let local = TaskId::new(NodeId::new("agent/a"));
        let ext1 = TaskId::new(NodeId::new("agent/b"));
        let ext2 = TaskId::new(NodeId::new("agent/c"));
        let task = Task {
            id: local.clone(),
            status: TaskStatus::Blocked,
            blocked_by: vec![(local.clone(), vec![ext1.clone(), ext2.clone()])],
            blocking: vec![],
            metadata: serde_json::Value::Null,
        };
        let deps = &task.blocked_by[0].1;
        assert_eq!(deps.len(), 2);
        assert!(deps.contains(&ext1));
        assert!(deps.contains(&ext2));
    }

    // [构造] blocking 有多个 entry：多个外部 task 依赖本地 task
    #[test]
    fn task_with_blocking() {
        let local = TaskId::new(NodeId::new("agent/b"));
        let ext_a = TaskId::new(NodeId::new("agent/a"));
        let ext_c = TaskId::new(NodeId::new("agent/c"));
        let task = Task {
            id: local.clone(),
            status: TaskStatus::InProgress,
            blocked_by: vec![],
            blocking: vec![(local.clone(), vec![ext_a.clone(), ext_c.clone()])],
            metadata: serde_json::Value::Null,
        };
        let waiters = &task.blocking[0].1;
        assert_eq!(waiters.len(), 2);
    }

    // [trait] Clone：克隆后与原值相等
    #[test]
    fn task_clone() {
        let task = Task::new(TaskId::new(NodeId::new("a")));
        assert_eq!(task, task.clone());
    }

    // [序列化] serde 往返：Task 含 blocked_by + blocking + metadata
    #[test]
    fn task_serialization_roundtrip() {
        let local = TaskId::new(NodeId::new("agent/a"));
        let ext = TaskId::new(NodeId::new("agent/b"));
        let task = Task {
            id: local.clone(),
            status: TaskStatus::Blocked,
            blocked_by: vec![(local.clone(), vec![ext.clone()])],
            blocking: vec![],
            metadata: serde_json::json!({"reason": "waiting for tool result"}),
        };
        let json = serde_json::to_string(&task).unwrap();
        let back: Task = serde_json::from_str(&json).unwrap();
        assert_eq!(task, back);
        assert_eq!(back.status, TaskStatus::Blocked);
        assert!(json.contains("waiting for tool result"));
    }
```

---

### State — 7 tests

```rust
    // ═══════════════════════════════════════════════════════════════
    // State — 7 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] new() 创建空的 messages 和 tasks
    #[test]
    fn state_new_is_empty() {
        let state = State::new();
        assert!(state.messages.is_empty());
        assert!(state.tasks.is_empty());
    }

    // [trait] Default：State::default() 等于 State::new()
    #[test]
    fn state_default_equals_new() {
        assert_eq!(State::default(), State::new());
    }

    // [边界] messages 和 tasks 为空：序列化/反序列化后仍为空
    #[test]
    fn state_empty_serialization_roundtrip() {
        let state = State::new();
        let json = serde_json::to_string(&state).unwrap();
        let back: State = serde_json::from_str(&json).unwrap();
        assert_eq!(state, back);
        assert!(json.contains("\"messages\""));
        assert!(json.contains("\"tasks\""));
    }

    // [构造] 含 messages 和 tasks 的 State 正确存储
    #[test]
    fn state_with_messages_and_tasks() {
        let msg = ModelMessage::new("user", "hello");
        let task = Task::new(TaskId::new(NodeId::new("engine/main")));
        let state = State {
            messages: vec![msg.clone()],
            tasks: vec![task.clone()],
        };
        assert_eq!(state.messages.len(), 1);
        assert_eq!(state.tasks.len(), 1);
        assert_eq!(state.messages[0], msg);
        assert_eq!(state.tasks[0], task);
    }

    // [trait] Clone：克隆后与原值相等
    #[test]
    fn state_clone() {
        let state = State {
            messages: vec![ModelMessage::new("system", "init")],
            tasks: vec![Task::new(TaskId::new(NodeId::new("a")))],
        };
        assert_eq!(state, state.clone());
    }

    // [序列化] State 含消息和任务，serde 往返后结构完整
    #[test]
    fn state_serialization_roundtrip_with_data() {
        let msg = ModelMessage::new("assistant", "response")
            .with_extra(serde_json::json!({"reasoning_content": "think"}));
        let owner = NodeId::new("engine/s1");
        let local = TaskId::new(owner);
        let task = Task {
            id: local,
            status: TaskStatus::InProgress,
            blocked_by: Vec::new(),
            blocking: Vec::new(),
            metadata: serde_json::json!({"desc": "A2A task"}),
        };
        let state = State {
            messages: vec![msg],
            tasks: vec![task],
        };
        let json = serde_json::to_string(&state).unwrap();
        let back: State = serde_json::from_str(&json).unwrap();
        assert_eq!(state, back);
        assert_eq!(back.messages.len(), 1);
        assert_eq!(back.tasks.len(), 1);
    }

    // [序列化] State 含双向锁完整场景：Message + Task blocked_by 参与 JSON 往返
    #[test]
    fn state_serialization_roundtrip_with_bidirectional_locks() {
        let a_owner = NodeId::new("agent/a");
        let b_owner = NodeId::new("agent/b");
        let task_a = TaskId::new(a_owner.clone());
        let task_b = TaskId::new(b_owner);

        let a_state = State {
            messages: vec![
                ModelMessage::new("user", "delegate task"),
                ModelMessage::new("assistant", "delegating to B")
                    .with_extra(serde_json::json!({"reasoning_content": "need B's help"})),
            ],
            tasks: vec![Task {
                id: task_a.clone(),
                status: TaskStatus::Blocked,
                blocked_by: vec![(task_a.clone(), vec![task_b.clone()])],
                blocking: vec![],
                metadata: serde_json::Value::Null,
            }],
        };

        let json = serde_json::to_string(&a_state).unwrap();
        let back: State = serde_json::from_str(&json).unwrap();
        assert_eq!(a_state, back);
        assert_eq!(back.tasks[0].status, TaskStatus::Blocked);
        let deps = &back.tasks[0].blocked_by[0].1;
        assert_eq!(deps.len(), 1);
    }
}
```

---

## 测试汇总

| 类型 | 测试数 | 覆盖角度 |
|------|--------|---------|
| Task | 8 | 构造(×2)、边界(×2)、构造含锁(×2)、Clone、序列化 |
| State | 7 | 构造、Default、边界(空序列化)、构造含数据、Clone、序列化、序列化含双向锁 |
| **合计** | **15** | |

---

## 与现有代码的关系

- 完全替换 `crates/arf-state/src/lib.rs` 占位内容
- `crates/arf-state/Cargo.toml` 新增 `serde` + `serde_json` 依赖
- 不修改 `arf-core` 或其他 crate

---

## 交付标准

- `cargo test --workspace` 全部通过（含已有 173 + 新增 15 = 188 tests）
- `cargo fmt --check` + `cargo clippy` 无警告
- `Task` / `State` 可序列化往返
- `arf-state` 仅依赖 `arf-core` + `serde` + `serde_json`，不依赖 Bus 或其他 ARF crate
