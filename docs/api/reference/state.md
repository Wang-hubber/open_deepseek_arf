# ARF State — 状态数据模型

> **Phase 2** · 纯数据结构 · `arf-core` + `arf-state` crate
>
> Rust-only，不暴露 Python API。Engine (Phase 4) 持有并操作 State。

---

## 概述

ARF State 定义了 Agent 运行时的持久化数据模型。所有结构体是**纯数据**——无业务方法，Engine 负责全部操作（状态转换、级联传播、持久化读写）。

两个crate 分层：

```
arf-core (共享类型, 零依赖)
  ├── TaskId       — A2A 任务标识
  ├── TaskStatus   — 6 状态生命周期
  └── ModelMessage — 对话消息

arf-state (aggregate, 仅依赖 arf-core + serde)
  ├── Task         — A2A 任务 + 双向锁
  └── State        — messages + tasks 聚合根
```

### 领域边界

```
Bus (实时流动)                   State (持久化)
  │                                │
  ├─ model_call 请求              │
  ├─ model_response               ├─→ messages (完整对话历史)
  │   (含 text 或 tool_calls)     │     ├─ role: "user"
  │                                │     ├─ role: "assistant" (+ tool_calls)
  ├─ tool_call 请求               │     └─ role: "tool" (+ tool_call_id)
  ├─ tool_result                  │
  │                                │
  └─ A2A 协调消息 ──────────────→ tasks (生命周期管理)
```

`messages` 存储**模型的视角的完整对话**——每一条都是 `ModelMessage`，包括用户的输入、模型的文本/工具调用回复、以及工具执行结果。这与 Bus 上流动的请求/响应消息是不同的概念：Bus 上的 `model_call` 是 Engine 发给 ModelAdapter 的请求，`model_response` 是 ModelAdapter 返回的结果；而 `State.messages` 存储的是模型看到的对话上下文，用于下一轮 `model_call` 时拼接 prompt。

---

## 核心概念

### 任务生命周期

```
created ──→ in_progress ──→ resolved    ┐
    │            │  ↑          failed     │ 终态
    │            │  │ 唤醒     cancelled  ┘ 不可逆
    │            │  │
    └──→ blocked ──┘
              │
              ├──→ cancelled (级联取消)
              └──→ failed    (节点离线)
```

`TaskStatus::is_terminal()` 判断终态（Resolved / Failed / Cancelled）。Engine 负责合法转换校验。

### 双向锁

Agent A 的 task_a1 等待 Agent B 的 task_b1, task_b2：

```
A 侧: task_a1.blocked_by = [(task_a1, [task_b1, task_b2])]   // "我等谁"
B 侧: task_b1.blocking  = [(task_b1, [task_a1])]            // "谁等我"
       task_b2.blocking  = [(task_b2, [task_a1])]
```

两边合起来可还原完整依赖链。锁同时存在于 A 和 B 两侧——A 声明等待，B 声明被等待。

### 供应商扩展

`ModelMessage.extra` 是 `serde_json::Value` 黑洞——供应商特有数据（DeepSeek 的 `reasoning_content`、Anthropic 的扩展思考）由 ModelAdapter 写入/消费。State 只存不读，Provider 迁移时旧数据不报错。

---

## 数据结构参考

### `TaskId`

`crates/arf-core/src/lib.rs` · 任务唯一标识

```rust
#[derive(Debug, Clone, Hash, Eq, PartialEq, Serialize, Deserialize)]
pub struct TaskId {
    pub id: Uuid,        // UUID v4，全局唯一，不承载业务信息
    pub owner: NodeId,   // 创建者，用于级联释放时定位归属节点
}
```

| 构造器 | 说明 |
|--------|------|
| `TaskId::new(owner: NodeId) -> Self` | 自动生成 UUID v4 |

| trait | 实现 |
|-------|------|
| `Display` | `uuid@owner`，如 `550e8400-...@engine/main` |
| `Hash`, `Eq` | 可做 HashMap key |

---

### `TaskStatus`

`crates/arf-core/src/lib.rs` · 任务生命周期状态

```rust
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum TaskStatus {
    Created,       // 初始态
    InProgress,    // 执行中
    Blocked,       // 等待外部依赖
    Resolved,      // 终态：成功完成
    Failed,        // 终态：执行失败
    Cancelled,     // 终态：被取消
}
```

| 方法 | 签名 | 说明 |
|------|------|------|
| `is_terminal()` | `&self -> bool` | Resolved / Failed / Cancelled 返回 `true` |

| trait | 实现 |
|-------|------|
| `Display` | snake_case 小写：`"created"`, `"in_progress"`, ... |
| `Serialize` | 序列化为 `"created"` 等 JSON 字符串 |

---

### `ModelMessage`

`crates/arf-core/src/lib.rs` · 对话消息

```rust
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelMessage {
    pub role: String,                              // "user" | "assistant" | "system" | "tool"
    pub content: String,                           // 消息正文
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,              // tool role 必需，其余 None(跳过序列化)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,                      // 可选显示名，None 时跳过序列化
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub extra: serde_json::Value,                  // 供应商黑洞，ModelAdapter 管理，Null 时跳过
}
```

| 构造器 | 说明 |
|--------|------|
| `ModelMessage::new(role, content)` | role 和 content 接受 `impl Into<String>`，其余默认 None/Null |
| `.with_tool_call_id(id)` | builder，设置 tool_call_id |
| `.with_name(name)` | builder，设置 name |
| `.with_extra(value)` | builder，设置 extra |

**序列化行为：** `tool_call_id`/`name` 为 `None` 时不出现在 JSON 中；`extra` 为 `Null` 时同理。旧数据无 `extra` 字段反序列化不报错（`serde(default)` 填 `Null`）。

```rust
// user 消息 — JSON 仅含 role + content
let msg = ModelMessage::new("user", "hello");
// → {"role":"user","content":"hello"}

// tool 消息 — 含 tool_call_id + name
let msg = ModelMessage::new("tool", r#"{"ok":true}"#)
    .with_tool_call_id("call_abc")
    .with_name("read_file");
// → {"role":"tool","content":"{\"ok\":true}","tool_call_id":"call_abc","name":"read_file"}

// assistant 消息 — ModelAdapter 存入 reasoning_content
let msg = ModelMessage::new("assistant", "answer")
    .with_extra(serde_json::json!({"reasoning_content": "let me think..."}));
// → {"role":"assistant","content":"answer","extra":{"reasoning_content":"let me think..."}}
```

---

### `Task`

`crates/arf-state/src/lib.rs` · A2A 任务

```rust
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Task {
    pub id: TaskId,
    pub status: TaskStatus,
    /// (本地 task, 依赖的外部 task 列表) — "我等谁"
    pub blocked_by: Vec<(TaskId, Vec<TaskId>)>,
    /// (本地 task, 被哪些外部 task 依赖) — "谁等我"
    pub blocking: Vec<(TaskId, Vec<TaskId>)>,
    pub metadata: serde_json::Value,
}
```

| 构造器 | 说明 |
|--------|------|
| `Task::new(id: TaskId) -> Self` | 初始状态 `Created`，锁为空，metadata 为 Null |
| `.with_metadata(value)` | builder，设置 metadata |

`blocked_by` / `blocking` 使用 `Vec` 而非 `HashMap`：JSON object key 必须为字符串，`TaskId` 是复合类型。Agent 依赖链短（通常 1-3 项），O(n) 查找开销可忽略。

> **注意：** `Task` 不派生 `Hash` / `Eq`——`serde_json::Value` 含 `f64::NAN`，不满足 `Eq` 契约。用 `PartialEq` 足够。

---

### `State`

`crates/arf-state/src/lib.rs` · Agent 会话状态

```rust
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct State {
    pub messages: Vec<ModelMessage>,  // 对话历史，Engine 恢复上下文用
    pub tasks: Vec<Task>,             // A2A 任务列表，含双向锁
}
```

| 构造器 | 说明 |
|--------|------|
| `State::new()` | 空 messages + tasks |
| `State::default()` | 等于 `new()` |

纯数据结构。Engine (Phase 4) 持有 `State` 实例，负责创建 task、状态转换、级联传播、持久化读写。ModelAdapter (Phase 5) 负责 `messages` 中 `extra` 字段的读写。

---

## 序列化示例

### 空 State

```json
{
  "messages": [],
  "tasks": []
}
```

### 含一条消息 + 一个 blocked task

```json
{
  "messages": [
    {
      "role": "assistant",
      "content": "delegating to B",
      "extra": {"reasoning_content": "need B's help"}
    }
  ],
  "tasks": [
    {
      "id": {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "owner": "agent/a"
      },
      "status": "blocked",
      "blocked_by": [[
        {"id": "660e8400-...", "owner": "agent/a"},
        [{"id": "770e8400-...", "owner": "agent/b"}]
      ]],
      "blocking": [],
      "metadata": null
    }
  ]
}
```

---

## Crate 依赖关系

```
serde + serde_json
      │
      ├── arf-core ──────── TaskId, TaskStatus, ModelMessage, NodeId, ...
      │     │
      │     └── arf-state ─── Task, State
      │
      └── (arf-bus, arf-agent, arf-engine — Phase 1/3/4)
```

`arf-state` 不依赖 `arf-bus` 或其他 ARF crate。纯数据无 I/O。

---

## 参考

- [Phase 2 State 设计文档](../v1.x/phase2_state/phase2-state-design.md) — 完整设计决策与任务拆解
- [任务 2.1 — arf-core 类型定义](../v1.x/phase2_state/task-2.1-arf-core-types.md) — TaskId / TaskStatus / ModelMessage 逐行解释
- [任务 2.2 — arf-state 实现](../v1.x/phase2_state/task-2.2-arf-state.md) — Task / State 逐行解释
- [Bus API 参考](bus.md) — Phase 1 消息总线
- [V1.x 路线图](../v1.x/2026-06-26-arfv1-roadmap.md) — 全 8 Phase 概览
