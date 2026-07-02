# SessionStore — ARF 会话持久化

> **Phase 8 F4** — Rust `arf-session` crate 的 Python 绑定。

## 1. 概念

`SessionStore` 提供**会话级**状态持久化。Engine 在每个 Checkpoint 位置自动 snapshot 到 store；App 启动时可选地 restore 来恢复中断的会话。

## 2. Rust API

```rust
use arf_session::{SessionStore, SqliteSessionStore, SessionMeta, SessionData};

// 创建 store
let store = Arc::new(SqliteSessionStore::new("sessions.db").await?);
// 或内存（测试用）
let store = Arc::new(SqliteSessionStore::in_memory().await?);

// 列出 sessions
let sessions: Vec<SessionMeta> = store.list().await?;

// 加载单个 session
let data: Option<SessionData> = store.load("sess-1").await?;

// 保存
store.save(&data).await?;

// 删除
store.delete("sess-1").await?;

// 在 Checkpoint 处快照
store.snapshot("sess-1", &state, &CheckpointSnapshot::new(...)).await?;
```

## 3. 集成方式

通过 `EngineBuilder` 注入：

```rust
let mut engine = EngineBuilder::new(buses)
    .with_session_store(store)
    .with_session_id("my-session")
    .build(config)
    .await?;
```

启动时自动从 `with_session_id` 加载已有 session 并恢复；若无则创建新的。

## 4. SessionData 结构

```rust
pub struct SessionData {
    pub meta: SessionMeta,         // id / title / status / counters
    pub state: arf_state::State,   // messages + tasks
    pub last_checkpoint: Option<CheckpointSnapshot>,
    pub config_snapshot: serde_json::Value,
}
```

## 5. CheckpointSnapshot

```rust
pub struct CheckpointSnapshot {
    pub checkpoint: Checkpoint,    // 5 个位置之一
    pub turn_index: usize,
    pub pending_messages: Vec<ModelMessage>,
    pub wait_events: Vec<WaitEvent>,
    pub captured_at: DateTime<Utc>,
    pub tasks_json: serde_json::Value,
}
```

## 6. 测试覆盖

- 15 个单元测试 in `crates/arf-session/src/lib.rs`
  - 构造 / save+load / list 排序 / delete / snapshot integrity
  - 序列化 / 反序列化 round-trip
  - 不存在 session 的错误处理

## 7. 何时使用

- **生产 app**（如 codecompass-fs）：必须 use，启用多会话存档 + 中断恢复
- **快速 prototype**：可以 skip（EngineBuilder 不传 `with_session_store`）
- **测试**：用 `in_memory()` 避免 IO
