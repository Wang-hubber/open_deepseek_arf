# Fix R7-L1 + R7-L2 — cancel 路径状态一致性

> **触发病灶**：R7-L1（mid-tool cancel 状态不一致）/ R7-L2（SessionStatus 缺中间态）
> **修复级别**：cross-cutting
> **优先级**：最高（session 实质不可恢复）
> **关联 commits**：无前置

---

## §1 病灶分析

### R7-L1 — mid-tool cancel 走 error 路径，tool_msg 不 push
- **位置**：`crates/arf-engine/src/engine.rs:610-710` (`do_tool_turn`) + `:576-594` (`do_tool_turns_concurrent`)
- **根因**：
  - `do_model_turn` 返回前 `assistant.message + tool_calls` 已入 `state.messages`（line 308 前后）
  - `do_tool_turns_concurrent` 在每 tool 循环前 check `cancel.is_cancelled()`：是则 push `Err(Stopped)` **不 push tool_msg**（line 584-588）
  - `do_tool_turn` 中 `send_and_await` 返回 `Err(Stopped)` 时 `?` 早退，也**不 push tool_msg**（line 684）
- **后果**：state.messages 含 `assistant[role=assistant, tool_calls=[X,Y,Z]]` 但无对应 `tool[role=tool, tool_call_id=X/Y/Z]` 消息。session reload 时 model adapter 报 400（tool_call_id 序列约束违反），session **实质不可恢复**

### R7-L2 — SessionStatus 缺 Cancelling 中间态
- **位置**：`crates/arf-session/src/lib.rs:25-34`（`SessionStatus` enum）
- **根因**：3 态 {Active, Completed, Interrupted}。`SqliteSessionStore::snapshot()` 强制 status='interrupted'（`lib.rs:469`）
- **后果**：
  - replay 策略被锁死为单一"中断恢复"路径
  - UI 无法区分"用户主动 cancel vs 进程崩溃 vs 工具挂"
  - app 触发 cancel 后无 framework 级状态标记，session 状态机粒度不足

---

## §2 修复方案

### R7-L1 — cancel 路径推 tool role 哨兵

**改动 1**：`crates/arf-engine/src/engine.rs` `do_tool_turns_concurrent` (line 576-594)

```rust
async fn do_tool_turns_concurrent(
    &mut self,
    state: &mut State,
    tool_calls: Vec<ToolCall>,
    cancel: CancellationToken,
) -> Vec<Result<serde_json::Value, RunError>> {
    let mut results = Vec::with_capacity(tool_calls.len());
    for tc in tool_calls {
        if cancel.is_cancelled() {
            // R7-L1 fix: push tool role sentinel for every cancelled tool
            // so state.messages has assistant + tool (cancelled) pairs.
            // Model on resume sees both messages — no dangling tool_call_id.
            let cancel_content = format!("[cancelled by user] {}", tc.name);
            let mut tool_msg = ModelMessage::new("tool", &cancel_content);
            tool_msg.tool_call_id = Some(tc.id.clone());
            tool_msg.name = Some(tc.name.clone());
            state.push_message(tool_msg);
            results.push(Err(RunError::Stopped));
            continue;
        }
        let r = self.do_tool_turn(state, tc, cancel.clone()).await;
        results.push(r);
    }
    results
}
```

**改动 2**：`do_tool_turn` (line 610-710) — 捕获 `send_and_await` 返回的 `Err(Stopped)`，push tool sentinel 后再返回

```rust
let response = match self.send_and_await(state, cid, msg, cancel.clone()).await {
    Ok(r) => r,
    Err(e @ RunError::Stopped) => {
        // R7-L1 fix: push tool role sentinel so model sees both halves
        let cancel_content = format!("[cancelled mid-execution] {}", tc.name);
        let mut tool_msg = ModelMessage::new("tool", &cancel_content);
        tool_msg.tool_call_id = Some(tc.id.clone());
        tool_msg.name = Some(tc.name.clone());
        state.push_message(tool_msg);
        return Err(e);
    }
    Err(e) => return Err(e),
};
```

**理由**：
- `state.messages` 在 cancel 退出时满足"每 assistant.tool_call 都有 tool 消息"约束
- model adapter 重放时不报 400，session 可恢复
- 哨兵内容 `[cancelled by user]` / `[cancelled mid-execution]` 区分 cancel 时机，便于审计

### R7-L2 — SessionStatus::Cancelling + 持久化保护

**改动 1**：`crates/arf-session/src/lib.rs` `SessionStatus` enum (line 25-34)

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum SessionStatus {
    /// In-progress; engine may have a pending checkpoint.
    Active,
    /// User requested cancel mid-round; engine winding down (R7-L2).
    /// State preserved for resume. Transitions to `Completed` (if app saves)
    /// or `Interrupted` (if engine snapshot forces it — but Cancelling wins).
    Cancelling,
    /// Round completed cleanly (model_response had no tool_calls).
    Completed,
    /// Process exited with an in-flight turn (Engine.run() interrupted
    /// by non-cancel cause: panic, OOM, parent signal, etc.).
    Interrupted,
}

impl SessionStatus {
    fn as_str(&self) -> &'static str {
        match self {
            SessionStatus::Active => "active",
            SessionStatus::Cancelling => "cancelling",
            SessionStatus::Completed => "completed",
            SessionStatus::Interrupted => "interrupted",
        }
    }
    fn from_str(s: &str) -> Result<Self, SessionError> {
        match s {
            "active" => Ok(SessionStatus::Active),
            "cancelling" => Ok(SessionStatus::Cancelling),
            "completed" => Ok(SessionStatus::Completed),
            "interrupted" => Ok(SessionStatus::Interrupted),
            other => Err(SessionError::Corrupt(format!(
                "unknown session status: {other}"
            ))),
        }
    }
}
```

**改动 2**：`SqliteSessionStore::snapshot` (line 460-478) — 保护 Cancelling 不被强制覆盖

```rust
async fn snapshot(
    &self,
    session_id: &str,
    state: &State,
    snapshot: &CheckpointSnapshot,
) -> Result<SnapshotEffects, SessionError> {
    let mut conn = self.conn.lock().await;
    let now = Utc::now().to_rfc3339();

    // R7-L2 fix: read current status; if Cancelling, preserve it.
    let current_status: String = conn
        .query_row(
            "SELECT status FROM sessions WHERE session_id = ?1",
            params![session_id],
            |row| row.get(0),
        )
        .optional()?
        .unwrap_or_else(|| "active".to_string());
    let forced_status = if current_status == "cancelling" {
        "cancelling"
    } else {
        "interrupted"
    };

    // 写 checkpoint row + 推 state_json + 更新 updated_at + 强制 status
    // (existing logic, but with `forced_status` instead of hard-coded 'interrupted')
    // ...
}
```

**应用层使用**：
```rust
// App 触发 cancel 前，主动标记 Cancelling
let mut data = store.load(&session_id).await?.unwrap();
data.meta.status = SessionStatus::Cancelling;
store.save(&data).await?;

// 之后 cancel token 触发 → engine 退出 → 后续 save/snapshot 都保留 Cancelling
```

---

## §3 测试

**测试位置**：`crates/arf-engine/src/tests/cancel_recovery.rs` (NEW) + `crates/arf-e2e/tests/interrupt.rs` 补充

| # | test | 角度 | 验证 |
|---|---|---|---|
| 1 | `cancel_mid_tool_pushes_sentinel_for_each_call` | [边界] | 3 tool_calls + cancel after tool 0 → state.messages 含 3 个 tool 消息（1 正常 + 2 哨兵） |
| 2 | `cancel_before_tool_pushes_sentinel` | [边界] | 2 tool_calls + cancel before tool 0 → 2 个 tool 哨兵 |
| 3 | `cancelled_session_reloadable_via_mock_model` | [方法] | mid-tool cancel + snapshot + reload → mock model 不报 400 |
| 4 | `session_status_cancelling_serde_roundtrip` | [序列化] | Cancelling JSON 双向一致 |
| 5 | `session_status_cancelling_preserved_through_snapshot` | [持久化] | 标 Cancelling → snapshot → 重新 load 仍 Cancelling |
| 6 | `session_status_completed_overrides_cancelling` | [持久化] | 标 Cancelling → app save(status=Completed) → 变 Completed |
| 7 | `cancel_during_tool_exec_pushes_mid_execution_sentinel` | [边界] | mid-tool cancel → tool_msg 内容含 `[cancelled mid-execution]` 标记 |

回归：`crates/arf-e2e/tests/interrupt.rs` 既有 6 个 test 应继续过

---

## §4 兼容性

- **R7-L1**：仅修改 `do_tool_turns_concurrent` + `do_tool_turn` 内部逻辑，对外 API 不变。cancel 仍返 `Err(Stopped)`。新增 1 个副作用：state.messages 多 1+ tool 哨兵
- **R7-L2**：SessionStatus 加 1 变体。向后兼容：旧 `save()` with status 字段仍 work（Cancelling 是新加的，旧 app 不会写）；旧 snapshot() 行为对 Active/Completed/Interrupted 完全不变。SqliteSessionStore 加 1 个 SELECT 检查 → 性能影响 < 1ms

---

## §5 验证清单

- [ ] `cargo build --workspace` 通过
- [ ] `cargo test -p arf-engine` 新增 4 test 通过
- [ ] `cargo test -p arf-session` 新增 3 test 通过 + 既有 89 test 不退化
- [ ] `cargo test --workspace --lib` 全过
- [ ] lesion-registry.md R7-L1/R7-L2 改 `FIXED（<hash>）` + 引用本文档
- [ ] round2-probe-summary.md 表格更新
- [ ] CHANGELOG.md 加 `[R7-L1+R7-L2] cancel 路径状态一致性修复`
