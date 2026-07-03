# audit-probe-9.10.3：5 Checkpoint 各调 `snapshot` 行为一致性端到端探查

> Task 9.10.3 探查产出 — **5 Checkpoint 位置（BeforeModelCall/AfterModelCall/BeforeToolExec/AfterToolExec/RoundEnd）各调 `snapshot` 行为是否一致？**
> 父 task doc：`docs/v1.x/phase9/task-9.10.3.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.10.1（EngineBuilder + SqliteSessionStore 端到端）
> **本 task 探查：5 位置触发数 + load 取 latest 一致性**

---

## §A 探查环境

- working tree：HEAD `141d74b`（task 9.10.2）+ uncommitted `crates/arf-e2e/tests/session_checkpoint_5pos.rs`
- 测试文件：`crates/arf-e2e/tests/session_checkpoint_5pos.rs`（3 test cases）
- 驱动：mock provider + `SqliteSessionStore::new(file_path)`（file 模式便于 second-conn 验证行数）
- 验证手法：直接 rusqlite::Connection 打开同一文件，count checkpoints 表行数
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test session_checkpoint_5pos -- --nocapture --test-threads=1
  ```
- 结果：**`3 passed; 0 failed; 2.05s`**
- 关键运行输出：
  ```
  test snapshot_fires_at_3_positions_no_tool ...
  [5pos] no_tool: checkpoints rows = 3 (expected 3)
  [5pos] no_tool: last_checkpoint = RoundEnd turn_index=1
  ok
  test snapshot_fires_with_tool ...
  [5pos] with_tool: checkpoints rows = 7 (expected 7)
  [5pos] with_tool: last_checkpoint = RoundEnd turn_index=3
  ok
  test load_returns_latest_snapshot_only ...
  [5pos] after run 1: rows=3, last_checkpoint turn_index=1
  [5pos] after run 2: rows=6, last_checkpoint turn_index=2
  [5pos] load returns latest: turn_index 1 → 2 ✓
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/session_checkpoint_5pos.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：1 round 无 tool → 3 fires

```
单元              : session_persist × §2.8（snapshot fires 数与位置）
能力等级           : D（PASS）
判定依据          : 1 round 无 tool → engine.rs:226-309 主循环触发
                   turn 1: BeforeModelCall / AfterModelCall (tool_calls.is_empty() → RoundEnd)
                   = 3 fires → checkpoints 表 3 行（实测 3 ✓）
                   load() 返回 last_checkpoint.checkpoint = RoundEnd ✓
file:line         : crates/arf-engine/src/engine.rs:226-309   run() 主循环
                   crates/arf-engine/src/engine.rs:330       snapshot_if_configured
                   crates/arf-session/src/lib.rs:305         load 读 checkpoints latest
```

### 单元 2：1 round 1 tool → 7 fires

```
单元              : session_persist × §2.8（snapshot fires 含 tool batch）
能力等级           : D（PASS）
判定依据          : 1 round 1 tool → 7 fires（2 inner turn）：
                   turn 1: BMC / AMC / BTE / ATE
                   turn 2: BMC / AMC / RE
                   checkpoints 表 7 行（实测 7 ✓）
                   load() 返回 last_checkpoint.checkpoint = RoundEnd, turn_index=3 ✓
file:line         : crates/arf-engine/src/engine.rs:282-297   tool 批 + BTE/ATE 单次触发
                   crates/arf-engine/src/engine.rs:285-287   "per-tool checkpoint 不并发触发"
```

### 单元 3：load 返回 latest snapshot

```
单元              : session_persist × §2.8（latest 取数）
能力等级           : D（PASS）
判定依据          : 多次 run → checkpoints 表累加（run1=3 → run2=6）
                   load() 返回 latest = run 2 的 last snapshot
                   turn_index 1 → 2（实测 ✓）
file:line         : crates/arf-session/src/lib.rs:305  "ORDER BY captured_at DESC LIMIT 1"
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `session_persist × §2.8`（5 位置 fires 数 no_tool） | **D** | 3 fires 实测 3 |
| `session_persist × §2.8`（5 位置 fires 数 with_tool） | **D** | 7 fires 实测 7 |
| `session_persist × §2.8`（load 取 latest） | **D** | captured_at DESC LIMIT 1 实测 |

---

## §D 病灶登记

**本 task 无新增 F-lesion**。

### 框架实际行为（按 spec §3.3 输出）

- `snapshot_if_configured` 在 5 位置统一触发（不区分位置）：**D**
- 1 round 无 tool → 3 fires (BMC, AMC, RE)：**D**
- 1 round 1 tool → 7 fires（BMC, AMC, BTE, ATE, BMC, AMC, RE）：**D**（tool 位置 batch-level 触发一次，非 per-tool）
- load() 取 latest 1 条（**不**保留 history）：**D**
- snapshot 路径强制 status = Interrupted（session/lib.rs:412）：**D**

### 注意事项（潜在 issue，非 lesion）

1. **History 不保留**——多次 snapshot 后旧记录仍存在（checkpoints 表 3 → 6 行），但 `load()` 只返回 latest。如果 app 想 replay history，**需自己写 query**（frame 未提供 `list_checkpoints(session_id)` API）。**建议**：SessionStore trait 加 `async fn list_checkpoints(&self, sid: &str) -> Vec<CheckpointSnapshot>`。
2. **Status 永远 Interrupted**——snapshot 路径强制 `status = 'interrupted'`（session/lib.rs:412），但 run() 正常完成时 status 应该是 Completed。**建议**：engine.rs:202 spawn 任务内根据 run 状态决定 status，或者 snapshot 路径不重写 status（保留 save() 设的 Active/Completed）。
3. **Snapshot 与 state mutation 顺序**——`evaluate_and_dispatch` 顶部（engine.rs:330）调 snapshot，**state 已包含**该位置 mutation（如 BMC 时 state 含 user 消息；AMC 时 state 含 assistant 消息），符合预期。

---

## §E 探查回归

- 9.10.1 / 9.10.2 既有 8 test pass
- 9.10.3 新增 3 test pass
- 与 F-010（app 必须预 save）/ F-011（save() 不持久化 last_checkpoint）**有交互**：本 task test 也预 save（否则 NotFound 静默 fail）。F-011 不影响本 task（last_checkpoint 由 snapshot() 写）。
- 综合：9.10 = 11 test（4+4+3），**全 pass**

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 5 位置各自 snapshot 一致性 | ✓ test 1/2 实测 3 / 7 fires |
| load 取 latest | ✓ test 3 实测 |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.10.3 探查显示 framework **5 Checkpoint 各调 `snapshot` 行为一致**（3/3 pass）—— 1 round
> no_tool 3 fires、1 round 1 tool 7 fires、load 取 latest by captured_at DESC LIMIT 1。这是 phase 9
> 持久化类别**首个 0 新 F-lesion** 的 task。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/session_checkpoint_5pos.rs`（~180 行，3 test cases）
- task doc：`docs/v1.x/phase9/task-9.10.3.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**
- 待 commit
