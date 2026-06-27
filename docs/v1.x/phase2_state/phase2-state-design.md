# Phase 2 — State 设计

> 父文档：`docs/v1.x/v1.x-design.md` §4、`docs/v1.x/2026-06-26-arfv1-roadmap.md`
> 依赖：Phase 1 (Bus) — 已完成
> 状态：✅ 已完成（任务 2.1–2.4）

## 定位

State 是**纯数据结构**，定义在两个 crate：

| Crate | 内容 | 职责 |
|-------|------|------|
| `arf-core` | `TaskId`, `TaskStatus`, `ModelMessage` | 共享类型，零依赖 |
| `arf-state` | `Task`, `State` | Aggregate，仅依赖 `arf-core` + `serde` + `serde_json` |

State 不包含业务方法——Engine (Phase 3) 持有 State 实例并负责所有操作（状态转换、级联传播、持久化时机）。

## 领域边界

| 概念 | 管理位置 | 职责 |
|------|---------|------|
| `messages` | State | 模型对话历史（经过 ModelAdapter 整理），Engine 恢复上下文用 |
| `tasks` | State | A2A 任务，有生命周期 + 双向锁 + 级联释放 |
| ReAct 循环消息（model_call/tool_call） | Bus | 在 Bus 上实时流动，不在 State 里存 |

`messages` 和 `tasks` 职责分开——前者管 ReAct 上下文恢复，后者管 A2A 协调。

## 最终类型设计

### `arf-core` 新增（任务 2.1）

```rust
// TaskId — A2A 任务标识，UUID + owner
#[derive(Debug, Clone, Hash, Eq, PartialEq, Serialize, Deserialize)]
pub struct TaskId {
    pub id: Uuid,
    pub owner: NodeId,
}
// Display: "uuid@owner"
// 构造: TaskId::new(owner) — UUID 自动生成

// TaskStatus — 6 状态 + is_terminal() 终态判断
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum TaskStatus {
    Created,
    InProgress,
    Blocked,
    Resolved,    // 终态
    Failed,      // 终态
    Cancelled,   // 终态
}

// ModelMessage — 对话消息，含供应商扩展点
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelMessage {
    pub role: String,                           // "user" | "assistant" | "system" | "tool"
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,           // tool role 必需
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,                   // 可选显示名
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub extra: serde_json::Value,               // 供应商特有数据黑洞（ModelAdapter 管理）
}
```

`extra` 字段设计决策：DeepSeek 的 `reasoning_content`、Anthropic 的扩展思考等供应商特有字段，State 只存不读，由 ModelAdapter 全权管理。比每个供应商加一个 Option 字段更可扩展，旧数据反序列化不报错（`serde(default)`）。

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

Engine 负责合法转换校验。`TaskStatus::is_terminal()` 判断终态（Resolved/Failed/Cancelled），终态不可逆。

### `arf-state` 实现（任务 2.2）

```rust
pub struct Task {
    pub id: TaskId,
    pub status: TaskStatus,
    /// (本地 task, 依赖的外部 task 列表)
    /// 如 taskA_1 → [taskB_1, taskB_2]
    pub blocked_by: Vec<(TaskId, Vec<TaskId>)>,
    /// (本地 task, 依赖它的外部 task 列表)
    /// B 侧: taskB_1 → [taskA_1]
    pub blocking: Vec<(TaskId, Vec<TaskId>)>,
    pub metadata: serde_json::Value,
}

pub struct State {
    pub messages: Vec<ModelMessage>,
    pub tasks: Vec<Task>,
}
```

`blocked_by`/`blocking` 使用 `Vec<(TaskId, Vec<TaskId>)>` 而非 `HashMap<TaskId, Vec<TaskId>>`：JSON object key 必须为字符串，`TaskId` 是复合类型无法直接做 key。Agent 依赖链短（通常 1-3 项），O(n) 查找开销可忽略，省去自定义 serde 的复杂度。

双向锁分两半存：`blocked_by`（我等谁）和 `blocking`（谁等我），各自存一份方向，两边合起来可还原完整依赖链。

构造器：
- `Task::new(id)` — 初始状态 `Created`，锁和元数据为空
- `Task::with_metadata(value)` — builder 设置元数据
- `State::new()` / `State::default()` — 空 messages + tasks

## 与后续 Phase 的关系

| Phase | 如何使用 State |
|-------|---------------|
| Phase 3 Engine | 持有 `State`，负责创建 task、状态转换、级联传播、持久化读写 |
| Phase 5 ModelAdapter | 读写 `extra` 字段；后续可加 `tool_calls: Vec<ToolCall>` |

## 任务完成情况

| # | 任务 | 产出 | 测试数 |
|---|------|------|--------|
| 2.1 | `arf-core` 新增类型 | `crates/arf-core/src/lib.rs` | 32 |
| 2.2 | `arf-state` 实现 | `crates/arf-state/src/lib.rs` | 15 |
| 2.3 | 单元测试 | 合并到 2.1 + 2.2 的 `#[cfg(test)]` | (含在上述) |
| 2.4 | 文档 | 本文件 + `task-2.1-arf-core-types.md` + `task-2.2-arf-state.md` | — |

## 交付标准

- [x] `cargo test --workspace` 全部通过（188 tests）
- [x] `TaskId` / `TaskStatus` / `ModelMessage` / `Task` / `State` 可序列化往返
- [x] `arf-state` 仅依赖 `arf-core` + `serde` + `serde_json`，不依赖 Bus 或其他 ARF crate
- [x] 旧数据兼容：`ModelMessage` 无 `extra` 字段反序列化不报错
- [x] 供应商扩展性：`extra` 字段支持 DeepSeek/Anthropic/OpenAI 特有数据
