# 任务 2.1：`arf-core` 新增类型

> Phase 2 — State 第一项任务
> 父文档：`docs/v1.x/phase2_state/phase2-state-design.md`

## 设计思路

Phase 2 需要三个新类型，全部定义在 `arf-core`（零依赖 crate）：

| 类型 | 用途 | 使用方 |
|------|------|--------|
| `TaskId` | A2A 任务标识，UUID + owner | State, Engine |
| `TaskStatus` | 任务 6 状态枚举 + 终态判断 | State, Engine |
| `ModelMessage` | 最小对话消息（Phase 5 扩展） | State, Engine, ModelAdapter |

现有依赖（`serde` + `serde_json` + `uuid`）已满足需求，`Cargo.toml` 无需修改。

---

## 代码实现

### `crates/arf-core/src/lib.rs` — 新增部分

以下三个模块追加在现有类型定义之后、`#[cfg(test)]` 之前。

---

#### TaskId

```rust
// ── TaskId ──────────────────────────────────────────────────────────

/// A2A task identifier.
///
/// UUID ensures global uniqueness across sessions.
/// `owner` records which node created the task.
#[derive(Debug, Clone, Hash, Eq, PartialEq, Serialize, Deserialize)]
pub struct TaskId {
    pub id: Uuid,
    pub owner: NodeId,
}
```

逐行：
- `#[derive(...)]` — `Debug` 打印，`Clone` 复制，`Hash + Eq + PartialEq` 做 HashMap key，`Serialize + Deserialize` JSON 序列化
- `id: Uuid` — 随机 UUID v4，全局唯一，不承载业务信息
- `owner: NodeId` — 记录任务创建者，用于级联释放时定位归属节点

```rust
impl TaskId {
    /// Create a new TaskId with a random UUID and the given owner.
    pub fn new(owner: NodeId) -> Self {
        Self {
            id: Uuid::new_v4(),
            owner,
        }
    }
}
```

逐行：
- `new(owner)` — 唯一构造方式，UUID 自动生成，不允许外部指定 id
- `Uuid::new_v4()` — 随机 UUID，Phase 0 已在 `Cargo.toml` 开启 `v4` feature

```rust
impl std::fmt::Display for TaskId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}@{}", self.id, self.owner)
    }
}
```

逐行：
- `Display` 格式 `uuid@owner`，如 `550e8400-e29b-41d4-a716-446655440000@engine/main`
- 方便日志和调试，一眼看出任务归属

---

#### TaskStatus

```rust
// ── TaskStatus ───────────────────────────────────────────────────────

/// Task lifecycle status.
///
/// ```text
/// created ──→ in_progress ──→ resolved
///     │            │  ↑          failed
///     │            │  │ 唤醒     cancelled
///     │            │  │
///     └──→ blocked ──┘
///               │
///               ├──→ cancelled (级联取消)
///               └──→ failed    (节点离线)
/// ```
///
/// Resolved / Failed / Cancelled are terminal states — irreversible.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum TaskStatus {
    Created,
    InProgress,
    Blocked,
    Resolved,
    Failed,
    Cancelled,
}
```

逐行：
- `#[derive(PartialEq)]` — 允许 `==` 比较，但不派生 `Eq`/`Hash`（枚举无数据时 `Eq` 也可派生，但后续可能加 payload，保守用 `PartialEq`）
- 六个变体对应设计文档的状态转换图
- 注释中的 ASCII 图直接来自父文档，保持单一真相源

```rust
impl TaskStatus {
    /// Returns true if this is a terminal state.
    ///
    /// Terminal states cannot transition further.
    pub fn is_terminal(&self) -> bool {
        matches!(self, Self::Resolved | Self::Failed | Self::Cancelled)
    }
}
```

逐行：
- `is_terminal()` — Engine 在执行状态转换前校验，终态拒绝任何转换
- `matches!` 宏 — 编译期保证所有变体被显式处理（若加新变体会编译失败，迫使更新此方法）

```rust
impl std::fmt::Display for TaskStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Self::Created => "created",
            Self::InProgress => "in_progress",
            Self::Blocked => "blocked",
            Self::Resolved => "resolved",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        };
        write!(f, "{s}")
    }
}
```

逐行：
- `Display` 输出 snake_case 小写，与 serde 序列化格式一致（serde 默认将 enum variant 序列化为小写字符串）

---

#### ModelMessage

```rust
// ── ModelMessage ─────────────────────────────────────────────────────

/// A single message in the model conversation history.
///
/// Minimal for Phase 2. Phase 5 (ModelAdapter) will extend with
/// `tool_calls`, `tool_call_id`, `name`, and other provider-specific fields.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelMessage {
    /// Role: `"user"`, `"assistant"`, `"system"`, or `"tool"`.
    pub role: String,
    /// Message content.
    pub content: String,
}
```

逐行：
- `role: String` — 四种标准角色，与 OpenAI/DeepSeek/Anthropic 对齐
- `content: String` — 消息正文，纯文本（Phase 5 可能扩展为 `Vec<ContentBlock>` 支持多模态）
- Phase 2 只是最小定义，`State.messages` 用它存储对话历史供 Engine 恢复上下文

```rust
impl ModelMessage {
    /// Create a new ModelMessage.
    pub fn new(role: impl Into<String>, content: impl Into<String>) -> Self {
        Self {
            role: role.into(),
            content: content.into(),
        }
    }
}
```

逐行：
- `new(role, content)` — 两个参数都用 `impl Into<String>`，调用方可传 `&str` 或 `String`
- 与 `Message::new()` 风格一致

---

## 测试

所有测试追加在 `crates/arf-core/src/lib.rs` 的 `#[cfg(test)] mod tests` 块内。

---

### TaskId — 8 tests

```rust
// ═══════════════════════════════════════════════════════════════
// TaskId — 8 tests
// ═══════════════════════════════════════════════════════════════

// [构造] new() 自动生成非 nil UUID，owner 正确赋值
#[test]
fn task_id_new_fills_id_and_owner() {
    let owner = NodeId::new("engine/main");
    let tid = TaskId::new(owner.clone());
    assert!(!tid.id.is_nil());
    assert_eq!(tid.owner, owner);
}

// [唯一性] 连续两次 new() 生成的 id 不同（UUID v4 随机性）
#[test]
fn task_id_unique_each_call() {
    let owner = NodeId::new("engine/main");
    let a = TaskId::new(owner.clone());
    let b = TaskId::new(owner.clone());
    assert_ne!(a.id, b.id);
}

// [trait] Display：格式为 "uuid@owner"
#[test]
fn task_id_display_format() {
    let owner = NodeId::new("engine/main");
    let tid = TaskId::new(owner.clone());
    let display = format!("{tid}");
    assert!(display.ends_with("@engine/main"));
    assert!(display.contains('-'), "should contain UUID dashes");
}

// [边界] owner 为空字符串：不 panic，Display 输出 "@"
#[test]
fn task_id_empty_owner() {
    let owner = NodeId::new("");
    let tid = TaskId::new(owner);
    let display = format!("{tid}");
    assert!(display.ends_with('@'));
}

// [trait] Eq：相同 id+owner 相等，不同 id 不等
#[test]
fn task_id_equality() {
    let owner = NodeId::new("a");
    let a = TaskId::new(owner.clone());
    let b = TaskId::new(owner.clone());
    assert_eq!(a, a);       // 同一个
    assert_ne!(a, b);       // 不同 UUID
}

// [trait] Clone：克隆后与原值相等
#[test]
fn task_id_clone_is_equal() {
    let tid = TaskId::new(NodeId::new("x"));
    let cloned = tid.clone();
    assert_eq!(tid, cloned);
}

// [trait] Hash：TaskId 可作为 HashMap key
#[test]
fn task_id_hashable() {
    let mut map = std::collections::HashMap::new();
    let tid = TaskId::new(NodeId::new("engine/main"));
    map.insert(tid.clone(), 42);
    assert_eq!(map.get(&tid), Some(&42));
    // 不同 TaskId 不应命中
    let other = TaskId::new(NodeId::new("engine/main"));
    assert_eq!(map.get(&other), None);
}

// [序列化] serde 往返：id + owner 一致性
#[test]
fn task_id_serialization_roundtrip() {
    let tid = TaskId::new(NodeId::new("engine/main"));
    let json = serde_json::to_string(&tid).unwrap();
    let back: TaskId = serde_json::from_str(&json).unwrap();
    assert_eq!(tid, back);
    // 验证 JSON 结构含 id 和 owner 字段
    assert!(json.contains("\"id\""));
    assert!(json.contains("\"owner\""));
}
```

---

### TaskStatus — 9 tests

```rust
// ═══════════════════════════════════════════════════════════════
// TaskStatus — 9 tests
// ═══════════════════════════════════════════════════════════════

// [覆盖] 6 种变体均可构造（编译期验证枚举完整）
#[test]
fn task_status_all_variants_construct() {
    let _ = TaskStatus::Created;
    let _ = TaskStatus::InProgress;
    let _ = TaskStatus::Blocked;
    let _ = TaskStatus::Resolved;
    let _ = TaskStatus::Failed;
    let _ = TaskStatus::Cancelled;
}

// [方法] is_terminal() — Created 不是终态
#[test]
fn task_status_created_is_not_terminal() {
    assert!(!TaskStatus::Created.is_terminal());
}

// [方法] is_terminal() — InProgress 不是终态
#[test]
fn task_status_in_progress_is_not_terminal() {
    assert!(!TaskStatus::InProgress.is_terminal());
}

// [方法] is_terminal() — Blocked 不是终态
#[test]
fn task_status_blocked_is_not_terminal() {
    assert!(!TaskStatus::Blocked.is_terminal());
}

// [方法] is_terminal() — Resolved / Failed / Cancelled 是终态
#[test]
fn task_status_terminal_states() {
    assert!(TaskStatus::Resolved.is_terminal());
    assert!(TaskStatus::Failed.is_terminal());
    assert!(TaskStatus::Cancelled.is_terminal());
}

// [trait] PartialEq：相同变体相等，不同不等
#[test]
fn task_status_equality() {
    assert_eq!(TaskStatus::Created, TaskStatus::Created);
    assert_ne!(TaskStatus::Created, TaskStatus::InProgress);
    assert_eq!(TaskStatus::Resolved, TaskStatus::Resolved);
    assert_ne!(TaskStatus::Failed, TaskStatus::Cancelled);
}

// [trait] Display：每个变体输出 snake_case 小写
#[test]
fn task_status_display() {
    assert_eq!(format!("{}", TaskStatus::Created), "created");
    assert_eq!(format!("{}", TaskStatus::InProgress), "in_progress");
    assert_eq!(format!("{}", TaskStatus::Blocked), "blocked");
    assert_eq!(format!("{}", TaskStatus::Resolved), "resolved");
    assert_eq!(format!("{}", TaskStatus::Failed), "failed");
    assert_eq!(format!("{}", TaskStatus::Cancelled), "cancelled");
}

// [trait] Clone：克隆后与原值相等
#[test]
fn task_status_clone() {
    let s = TaskStatus::InProgress;
    assert_eq!(s, s.clone());
}

// [序列化] 每个变体 serde 往返：JSON 字符串 ↔ enum 一致
#[test]
fn task_status_serialization_roundtrip() {
    for status in [
        TaskStatus::Created,
        TaskStatus::InProgress,
        TaskStatus::Blocked,
        TaskStatus::Resolved,
        TaskStatus::Failed,
        TaskStatus::Cancelled,
    ] {
        let json = serde_json::to_string(&status).unwrap();
        let back: TaskStatus = serde_json::from_str(&json).unwrap();
        assert_eq!(status, back);
    }
}
```

---

### ModelMessage — 7 tests

```rust
// ═══════════════════════════════════════════════════════════════
// ModelMessage — 7 tests
// ═══════════════════════════════════════════════════════════════

// [构造] new() 正确赋值 role 和 content
#[test]
fn model_message_new_sets_fields() {
    let msg = ModelMessage::new("user", "hello world");
    assert_eq!(msg.role, "user");
    assert_eq!(msg.content, "hello world");
}

// [构造] new() 接受 owned String（Into<String> trait）
#[test]
fn model_message_new_from_string() {
    let role = String::from("assistant");
    let content = String::from("response");
    let msg = ModelMessage::new(role, content);
    assert_eq!(msg.role, "assistant");
    assert_eq!(msg.content, "response");
}

// [边界] role 为空字符串：不 panic
#[test]
fn model_message_empty_role() {
    let msg = ModelMessage::new("", "content");
    assert_eq!(msg.role, "");
    assert_eq!(msg.content, "content");
}

// [边界] content 为空字符串：不 panic
#[test]
fn model_message_empty_content() {
    let msg = ModelMessage::new("system", "");
    assert_eq!(msg.role, "system");
    assert_eq!(msg.content, "");
}

// [trait] PartialEq：相同字段相等，不同不等
#[test]
fn model_message_equality() {
    let a = ModelMessage::new("user", "hello");
    let b = ModelMessage::new("user", "hello");
    let c = ModelMessage::new("user", "world");
    assert_eq!(a, b);
    assert_ne!(a, c);
}

// [trait] Clone：克隆后与原值相等
#[test]
fn model_message_clone() {
    let msg = ModelMessage::new("assistant", "reply");
    assert_eq!(msg, msg.clone());
}

// [序列化] serde 往返：role + content 一致性
#[test]
fn model_message_serialization_roundtrip() {
    let msg = ModelMessage::new("tool", r#"{"result": "ok"}"#);
    let json = serde_json::to_string(&msg).unwrap();
    let back: ModelMessage = serde_json::from_str(&json).unwrap();
    assert_eq!(msg, back);
    assert!(json.contains("\"role\""));
    assert!(json.contains("\"content\""));
}
```

---

## 测试汇总

| 类型 | 测试数 | 覆盖角度 |
|------|--------|---------|
| TaskId | 8 | 构造、唯一性、Display、边界(空owner)、Eq、Clone、Hash、序列化 |
| TaskStatus | 9 | 覆盖(6变体)、is_terminal(×5)、Eq、Display(×6)、Clone、序列化(×6) |
| ModelMessage | 7 | 构造(×2)、边界(空role)、边界(空content)、Eq、Clone、序列化 |
| **合计** | **24** | |

---

## 与现有代码的关系

- 新增代码追加在 `SendError` 之后、`#[cfg(test)] mod tests` 之前
- 新增测试追加在现有测试之后（`MessageFilter` 测试下方）
- 不修改任何现有类型和方法
- `Cargo.toml` 不变——所需依赖已全部存在

---

## 交付标准

- `cargo test --workspace` 全部通过（含已有 60+ tests + 新增 24 tests）
- `cargo fmt --check` + `cargo clippy` 无警告
- TaskId / TaskStatus / ModelMessage 可序列化往返
- TaskStatus 终态判断逻辑正确
