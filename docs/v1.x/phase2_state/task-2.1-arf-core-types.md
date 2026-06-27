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
/// `extra` carries provider-specific opaque data (e.g., DeepSeek
/// `reasoning_content`). State stores it as-is; ModelAdapter reads/writes
/// it — no other component interprets it. Phase 5 (ModelAdapter) will add
/// `tool_calls: Vec<ToolCall>` for assistant parallel tool invocations.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelMessage {
    /// Role: `"user"`, `"assistant"`, `"system"`, or `"tool"`.
    pub role: String,
    /// Message content.
    pub content: String,
    /// Required when role is "tool" — the ID of the tool call this result belongs to.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    /// Optional display name (e.g., function name for tool role, author for user).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// Provider-specific opaque data. Managed entirely by ModelAdapter.
    /// State stores it without interpretation. Default to `Value::Null`.
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub extra: serde_json::Value,
}
```

逐行：
- `role: String` — 四种标准角色，与 OpenAI/DeepSeek/Anthropic 对齐
- `content: String` — 消息正文，纯文本（Phase 5 可能扩展为 `Vec<ContentBlock>` 支持多模态）
- `tool_call_id: Option<String>` — tool role 必需关联到 assistant 的 tool_call；其余角色为 `None`，不序列化输出
- `name: Option<String>` — 可选显示名，如 tool 的函数名或 user 的发送者名；`None` 时不序列化
- `extra: serde_json::Value` — 供应商特有数据黑洞。State 只存不读，ModelAdapter 全权管理。DeepSeek 塞 `{"reasoning_content":"..."}`，Anthropic 塞它的东西，OpenAI 就是 `null`。`serde(default)` 保证反序列化旧数据（无此字段）时不报错，`skip_serializing_if` 保证 `null` 时不输出
- `#[serde(skip_serializing_if)]` — 两个 Option 字段在 `None` 时跳过，`extra` 在 `null` 时跳过，保证 user/assistant/system 消息 JSON 干净

```rust
impl ModelMessage {
    /// Create a ModelMessage with role and content.
    ///
    /// `tool_call_id`, `name`, and `extra` default to None/Null;
    /// set them via the builder-style methods below.
    pub fn new(role: impl Into<String>, content: impl Into<String>) -> Self {
        Self {
            role: role.into(),
            content: content.into(),
            tool_call_id: None,
            name: None,
            extra: serde_json::Value::Null,
        }
    }

    /// Set `tool_call_id` (builder style).
    pub fn with_tool_call_id(mut self, id: impl Into<String>) -> Self {
        self.tool_call_id = Some(id.into());
        self
    }

    /// Set `name` (builder style).
    pub fn with_name(mut self, name: impl Into<String>) -> Self {
        self.name = Some(name.into());
        self
    }

    /// Set `extra` (builder style). Managed by ModelAdapter.
    pub fn with_extra(mut self, extra: serde_json::Value) -> Self {
        self.extra = extra;
        self
    }
}
```

逐行：
- `new(role, content)` — 最小构造，可选字段默认 `None`/`Null`
- `with_tool_call_id(id)` / `with_name(name)` / `with_extra(value)` — builder 风格链式设置
- `with_extra` 接受 `serde_json::Value`，ModelAdapter 构造好后传入，State 不关心内部结构

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

### ModelMessage — 15 tests

```rust
// ═══════════════════════════════════════════════════════════════
// ModelMessage — 15 tests
// ═══════════════════════════════════════════════════════════════

// [构造] new() 正确赋值 role 和 content，可选字段默认为 None/Null
#[test]
fn model_message_new_sets_fields() {
    let msg = ModelMessage::new("user", "hello world");
    assert_eq!(msg.role, "user");
    assert_eq!(msg.content, "hello world");
    assert_eq!(msg.tool_call_id, None);
    assert_eq!(msg.name, None);
    assert_eq!(msg.extra, serde_json::Value::Null);
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

// [构造] builder: with_tool_call_id() 设置 tool_call_id
#[test]
fn model_message_with_tool_call_id() {
    let msg = ModelMessage::new("tool", "result")
        .with_tool_call_id("call_abc123");
    assert_eq!(msg.tool_call_id, Some("call_abc123".into()));
    assert_eq!(msg.role, "tool");
}

// [构造] builder: with_name() 设置 name
#[test]
fn model_message_with_name() {
    let msg = ModelMessage::new("tool", "result")
        .with_name("read_file");
    assert_eq!(msg.name, Some("read_file".into()));
}

// [构造] builder: 链式调用同时设置三个可选字段
#[test]
fn model_message_builder_chained() {
    let msg = ModelMessage::new("tool", r#"{"status": "ok"}"#)
        .with_tool_call_id("call_xyz")
        .with_name("search")
        .with_extra(serde_json::json!({"reasoning_content": "let me think..."}));
    assert_eq!(msg.tool_call_id, Some("call_xyz".into()));
    assert_eq!(msg.name, Some("search".into()));
    assert_eq!(msg.extra["reasoning_content"], "let me think...");
}

// [边界] role 为空字符串：不 panic
#[test]
fn model_message_empty_role() {
    let msg = ModelMessage::new("", "content");
    assert_eq!(msg.role, "");
}

// [边界] content 为空字符串：不 panic
#[test]
fn model_message_empty_content() {
    let msg = ModelMessage::new("system", "");
    assert_eq!(msg.role, "system");
    assert_eq!(msg.content, "");
}

// [trait] PartialEq：相同字段相等，不同不等（含可选字段）
#[test]
fn model_message_equality() {
    let a = ModelMessage::new("user", "hello");
    let b = ModelMessage::new("user", "hello");
    let c = ModelMessage::new("user", "world");
    let d = ModelMessage::new("tool", "hello").with_tool_call_id("id1");
    let e = ModelMessage::new("tool", "hello").with_tool_call_id("id1");
    let f = ModelMessage::new("tool", "hello").with_tool_call_id("id2");
    assert_eq!(a, b);
    assert_ne!(a, c);
    assert_eq!(d, e);
    assert_ne!(d, f);
}

// [trait] Clone：克隆后与原值相等（含可选字段）
#[test]
fn model_message_clone() {
    let msg = ModelMessage::new("tool", "reply")
        .with_tool_call_id("call_1")
        .with_name("run")
        .with_extra(serde_json::json!({"key": "value"}));
    assert_eq!(msg, msg.clone());
}

// [序列化] user 消息：可选字段为 None/Null 时不输出到 JSON
#[test]
fn model_message_serialization_user_skips_optionals() {
    let msg = ModelMessage::new("user", "hello");
    let json = serde_json::to_string(&msg).unwrap();
    let back: ModelMessage = serde_json::from_str(&json).unwrap();
    assert_eq!(msg, back);
    assert!(!json.contains("tool_call_id"));
    assert!(!json.contains("\"name\""));
    assert!(!json.contains("\"extra\""));
}

// [序列化] tool 消息：可选字段有值时输出到 JSON
#[test]
fn model_message_serialization_tool_with_options() {
    let msg = ModelMessage::new("tool", r#"{"result": "ok"}"#)
        .with_tool_call_id("call_abc")
        .with_name("search");
    let json = serde_json::to_string(&msg).unwrap();
    let back: ModelMessage = serde_json::from_str(&json).unwrap();
    assert_eq!(msg, back);
    assert!(json.contains("tool_call_id"));
    assert!(json.contains("call_abc"));
    assert!(json.contains("\"name\""));
    assert!(json.contains("search"));
}

// [序列化] tool 消息带 extra：extra 非 null 时输出到 JSON
#[test]
fn model_message_serialization_with_extra() {
    let msg = ModelMessage::new("assistant", "answer")
        .with_extra(serde_json::json!({"reasoning_content": "step 1: ..."}));
    let json = serde_json::to_string(&msg).unwrap();
    let back: ModelMessage = serde_json::from_str(&json).unwrap();
    assert_eq!(msg, back);
    assert!(json.contains("\"extra\""));
    assert!(json.contains("reasoning_content"));
}

// [兼容] 旧数据无 extra 字段：反序列化不报错，extra 为 Null
#[test]
fn model_message_deserialize_missing_extra() {
    let json = r#"{"role":"user","content":"hello"}"#;
    let msg: ModelMessage = serde_json::from_str(json).unwrap();
    assert_eq!(msg.role, "user");
    assert_eq!(msg.extra, serde_json::Value::Null);
}

// [边界] tool_call_id 为空字符串：序列化往返后仍为空字符串
#[test]
fn model_message_tool_call_id_empty_string() {
    let msg = ModelMessage::new("tool", "x").with_tool_call_id("");
    let json = serde_json::to_string(&msg).unwrap();
    let back: ModelMessage = serde_json::from_str(&json).unwrap();
    assert_eq!(back.tool_call_id, Some("".into()));
}

// [序列化] extra 嵌套对象（2层 + 数组 + null）：结构不丢失
#[test]
fn model_message_extra_deeply_nested() {
    let extra = serde_json::json!({
        "reasoning_content": "think",
        "meta": {"tokens": 42, "tags": ["a", null, "b"]}
    });
    let msg = ModelMessage::new("assistant", "ok").with_extra(extra);
    let json = serde_json::to_string(&msg).unwrap();
    let back: ModelMessage = serde_json::from_str(&json).unwrap();
    assert_eq!(msg.extra, back.extra);
    assert_eq!(back.extra["meta"]["tokens"], 42);
    assert_eq!(back.extra["meta"]["tags"][1], serde_json::Value::Null);
}
```

---

## 测试汇总

| 类型 | 测试数 | 覆盖角度 |
|------|--------|---------|
| TaskId | 8 | 构造、唯一性、Display、边界(空owner)、Eq、Clone、Hash、序列化 |
| TaskStatus | 9 | 覆盖(6变体)、is_terminal(×5)、Eq、Display(×6)、Clone、序列化(×6) |
| ModelMessage | 15 | 构造(×2)、builder(×3)、边界(×2)、Eq、Clone、序列化user(跳过所有可选)、序列化tool、序列化extra、兼容(旧数据无extra)、边界(tool_call_id空)、边界(extra深层嵌套) |
| **合计** | **32** | |

---

## 与现有代码的关系

- 新增代码追加在 `SendError` 之后、`#[cfg(test)] mod tests` 之前
- 新增测试追加在现有测试之后（`MessageFilter` 测试下方）
- 不修改任何现有类型和方法
- `Cargo.toml` 不变——所需依赖已全部存在

---

## 交付标准

- `cargo test --workspace` 全部通过（含已有 60+ tests + 新增 32 tests）
- `cargo fmt --check` + `cargo clippy` 无警告
- TaskId / TaskStatus / ModelMessage 可序列化往返
- TaskStatus 终态判断逻辑正确
