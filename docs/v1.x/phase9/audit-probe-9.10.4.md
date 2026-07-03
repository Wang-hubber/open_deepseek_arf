# audit-probe-9.10.4：跨 session_id load / restore 端到端探查

> Task 9.10.4 探查产出 — **单 SqliteSessionStore 持多 session，跨 session_id 是否互不串台？**
> 父 task doc：`docs/v1.x/phase9/task-9.10.4.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.10.1（EngineBuilder + SqliteSessionStore 端到端）
> **本 task 探查：多 session 互不干扰 + delete CASCADE + snapshot 不影响其他**

---

## §A 探查环境

- working tree：HEAD `ff529a2`（task 9.10.3）+ uncommitted `crates/arf-e2e/tests/session_multi_id.rs`
- 测试文件：`crates/arf-e2e/tests/session_multi_id.rs`（3 test cases）
- 驱动：直接 `SqliteSessionStore` API（in_memory + file 模式，test 2 用 file 让 second-conn 验证 CASCADE）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test session_multi_id -- --nocapture --test-threads=1
  ```
- 结果：**`3 passed; 0 failed; 0.01s`**
- 关键运行输出：
  ```
  test multiple_sessions_isolated_in_one_store ...
  [multi] 3 sessions isolated: titles + messages all distinct ✓
  [multi] load nonexistent → None ✓
  ok
  test snapshot_other_session_does_not_touch ...
  [multi] A snapshot applied: messages=2, round_count=2 ✓
  [multi] B untouched: messages=1, no checkpoint ✓
  [multi] C untouched: messages=1, no checkpoint ✓
  ok
  test delete_one_session_keeps_others ...
  [multi] pre-delete: B exists with last_checkpoint ✓
  [multi] post-delete: load(B) = None ✓
  [multi] post-delete: load(A/C) = Some ✓
  [multi] post-delete: list 2 sessions (A, C) ✓
  [multi] B's checkpoints CASCADE deleted (0 rows) ✓
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/session_multi_id.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：多 session 互不串台

```
单元              : session_persist × §2.8（多 session 隔离）
能力等级           : D（PASS）
判定依据          : 3 session（不同 sid/title/content）save 到同一 store
                   → 各自 load 各自数据（titles + messages 全 distinct）✓
                   → load 不存在 sid → None（不与现存 session 串台）✓
file:line         : crates/arf-session/src/lib.rs:337-370   save ON CONFLICT(sid) DO UPDATE
                   crates/arf-session/src/lib.rs:283-298    load WHERE session_id = ?1
```

### 单元 2：delete CASCADE 跨表

```
单元              : session_persist × §2.8（delete 级联）
能力等级           : D（PASS）
判定依据          : 3 session + snapshot B（写 B 的 checkpoints）→ delete B
                   → load(B) None ✓
                   → load(A/C) Some ✓
                   → list 2 sessions (A, C) ✓
                   → B 的 checkpoints 表 0 行（ON DELETE CASCADE 工作）✓
file:line         : crates/arf-session/src/lib.rs:218-244   init_schema 建表 + ON DELETE CASCADE
                   crates/arf-session/src/lib.rs:372-380    delete 双表 DELETE
```

### 单元 3：snapshot 隔离（不影响其他 session）

```
单元              : session_persist × §2.8（snapshot 隔离）
能力等级           : D（PASS）
判定依据          : 3 session save 后，snapshot A（写 A 的 checkpoints + UPDATE A.state_json）
                   → load(A).state 反映 snapshot 状态（2 messages, round_count=2, last_checkpoint Some）✓
                   → load(B).state **未**变（1 message, round_count=1, last_checkpoint None）✓
                   → load(C).state **未**变（1 message, round_count=1, last_checkpoint None）✓
file:line         : crates/arf-session/src/lib.rs:388-416    snapshot 单 session 隔离
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `session_persist × §2.8`（多 session 隔离） | **D** | 3 session 互不串台 |
| `session_persist × §2.8`（delete CASCADE） | **D** | delete session + 级联删 checkpoints |
| `session_persist × §2.8`（snapshot 隔离） | **D** | snapshot 1 个不影响其他 |

---

## §D 病灶登记

**本 task 无新增 F-lesion**。

### 框架实际行为（按 spec §3.3 输出）

- 多 session 互不串台（save / load / snapshot / delete 各自独立）：**D**
- `ON DELETE CASCADE` 工作（session 删除时 checkpoints 自动删）：**D**
- snapshot 单 session 隔离（写一个不影响其他）：**D**
- PRIMARY KEY (session_id) 隔离无冲突：sessions 表 session_id TEXT PRIMARY KEY；checkpoints 表 PRIMARY KEY (session_id, captured_at)

### 注意事项（潜在 issue，非 lesion）

1. **删除时双表 DELETE 显式**（session/lib.rs:374-375）—— `DELETE FROM checkpoints WHERE session_id = ?1` 然后 `DELETE FROM sessions WHERE session_id = ?1`。**实际依赖** `ON DELETE CASCADE`（session/lib.rs:238）可省第二行——**保留**作为 "不依赖 schema cascade" 的安全做法，**合理**。
2. **list 按 updated_at DESC**（session/lib.rs:253）—— 排序键由 save 决定。snapshot 也 UPDATE updated_at（session/lib.rs:411），所以 snapshot 频繁的 session 会跑到 list 顶部。**合理**（snapshot 表示 session 活跃）。
3. **没有 list_checkpoints(session_id) API**（见 9.10.3 §D 注意事项 1）—— 本 task 不再重复。

---

## §E 探查回归

- 9.10.1 / 9.10.2 / 9.10.3 既有 11 test pass
- 9.10.4 新增 3 test pass
- 综合：9.10 = 14 test（4+4+3+3），**全 pass**
- 与 F-010 / F-011 病灶**无关**——本 task 不涉及 save/snapshot 字段持久化语义，只验证隔离性

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 多 session 互不串台 | ✓ test 1 pass |
| delete 1 个不影响其他 + CASCADE | ✓ test 2 pass |
| snapshot 1 个不影响其他 | ✓ test 3 pass |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.10.4 探查显示 framework **跨 session_id load / restore** 端到端 work（3/3 pass）——
> PRIMARY KEY 隔离、ON DELETE CASCADE 跨表、snapshot 单 session 范围均正确。这是 phase 9
> 持久化类别**连续 2 个 0 新 F-lesion** 的 task（继 9.10.3 后）。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/session_multi_id.rs`（~165 行，3 test cases）
- task doc：`docs/v1.x/phase9/task-9.10.4.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**
- 待 commit
