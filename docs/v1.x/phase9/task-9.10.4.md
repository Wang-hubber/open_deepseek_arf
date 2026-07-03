# 任务 9.10.4：跨 session_id load / restore

> Phase 9 — 9.10 H 持久化大类 · 第 4 task（依赖 9.10.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.10.1（EngineBuilder + SqliteSessionStore 端到端）
> 输出物：`docs/v1.x/phase9/audit-probe-9.10.4.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.10.1-9.10.3 探查了单个 session 的端到端持久化。**本 task (9.10.4) 探查跨 session_id 行为——**
单 SqliteSessionStore 持多 session，list / load / delete / 各自独立，跨 session_id 不串台。

**Framework 现状**（待探查确认）：
- `SqliteSessionStore` 单 DB 文件持 N session（按 session_id 区分）
- `list()` 走 `ORDER BY updated_at DESC`（session/lib.rs:253）
- `load(session_id)` 走 `WHERE session_id = ?1`（session/lib.rs:288）
- `delete(session_id)` 走 `DELETE FROM sessions` + `DELETE FROM checkpoints` 级联
- `snapshot(session_id, ...)` 检查 sessions 存在性后写 checkpoints + UPDATE sessions

**关键探查问题**（不预设答案）：
1. 单 store 持 N session（不同 session_id）→ 各自 save/load 互不影响？
2. list 跨多 session 全部列出？按 updated_at DESC 排序？
3. delete 一个 session_id → 不影响其他 session_id？
4. snapshot 一个 session_id → 写该 session 的 checkpoints + UPDATE 该 session 的 state_json？其他 session 不动？
5. `ON DELETE CASCADE` 级联（session/lib.rs:238）真正 work？delete session 时 checkpoints 自动删？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-session/src/lib.rs` 单测已测 list_orders_by_updated_desc / save_overwrites_existing
- **本 task 不重复**：单 session 行为
- **本 task 聚焦**：多 session 跨 ID 互不串台 + delete 级联

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

`session_multi_id.rs`，3 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `multiple_sessions_isolated_in_one_store` | 单 store 持 3 session（不同 sid + title）→ 各自 save/load 互不影响；list 3 个 |
| 2 | `delete_one_session_keeps_others` | 3 session → delete 1 个 → load(deleted) None、load(others) Some、list 2 个 |
| 3 | `snapshot_other_session_does_not_touch` | 3 session → snapshot 1 个 → load(snapshotted) state 变；load(others) state 不变 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "WHERE session_id\|ON DELETE CASCADE\|ORDER BY updated_at" crates/arf-session/src/lib.rs
```

逐行解释：
- `save` `ON CONFLICT(session_id) DO UPDATE`：每 session 独立 row，互不覆盖（除非 sid 相同）
- `load` `WHERE session_id = ?1`：按 sid 隔离
- `delete` 双表 DELETE + `ON DELETE CASCADE` 级联
- `snapshot` 先 `SELECT 1 FROM sessions WHERE session_id = ?1` 检查存在性

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group5-persist
cargo test -p arf-e2e --test session_multi_id -- --nocapture --test-threads=1 2>&1 | tee /tmp/session_multi_id_run.log
```

逐行解释：
- 3 test 应全过（直接 SqliteSessionStore API + 多 session 操作）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/session_multi_id_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 3 个单元的判定。

按 §4 跑 signals：
- A1：每 session_id 的 CRUD 是 atomic 化？
- A2：cross-session 行为（如 snapshot 1 个不影响其他）正交？
- A3：session_id 作为 primary key 数据唯一？
- A4：delete 级联 + 隔离在单一接缝完成？

**C. 输出**：`audit-probe-9.10.4.md`。
