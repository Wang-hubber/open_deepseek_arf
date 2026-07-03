# 任务 9.10.2：SessionMeta / SessionData 序列化字段

> Phase 9 — 9.10 H 持久化大类 · 第 2 task（依赖 9.10.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.10.1（EngineBuilder + SqliteSessionStore 端到端）
> 输出物：`docs/v1.x/phase9/audit-probe-9.10.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.10.1 验证了端到端 session 持久化。**本 task (9.10.2) 探查 SessionMeta / SessionData 的序列化字段——**
framework 把哪些字段写进 DB？哪些被吞？round-trip 后字段名/类型/值是否一致？

**Framework 现状**（待探查确认）：
- `SessionMeta`：session_id, title, created_at, updated_at, round_count, turn_count, status, current_round
- `SessionData`：meta + state + last_checkpoint + config_snapshot
- `SqliteSessionStore::save` 用 `serde_json::to_string` 序列化 state + config_snapshot
- `init_schema` 字段：session_id, title, created_at, updated_at, round_count, turn_count, status, current_round, state_json, config_json
- `checkpoints` 表：session_id, captured_at, payload_json

**关键探查问题**（不预设答案）：
1. `SessionMeta` 8 字段是否全部持久化？哪些走 meta 列？哪些走 state_json 内嵌？
2. `SessionData` 4 字段在 DB 中如何分布？`state` 整个走 `state_json`（state 含 messages + over_view + wait_events），`config_snapshot` 走 `config_json`，`last_checkpoint` 走 `checkpoints` 表（按 captured_at DESC LIMIT 1）？
3. `serde_json::to_string(&data)` 的 JSON 形状 vs DB 行 —— round-trip 一致？
4. SessionStatus 3 variant (Active/Completed/Interrupted) DB 中是 string 列？
5. `current_round: Option<usize>` NULL 与 Some 区分？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-session/src/lib.rs` 单测已覆盖 SessionStatus round-trip / save+load round-trip / corrupt 错误
- `crates/arf-e2e/tests/session_persist.rs`（9.10.1）已覆盖端到端 load
- **本 task 不重复**：单测字段 round-trip
- **本 task 聚焦**：直接对 SqliteSessionStore 的"写入 vs 读出"对比——验证每个字段都端到端可见，无字段被吞

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

`session_serde_fields.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `session_meta_all_8_fields_persisted` | 预 save SessionMeta（8 字段全填）→ load → 逐字段断言值/类型/option 状态 |
| 2 | `session_data_4_fields_distributed_correctly` | 预 save SessionData（meta + state + last_checkpoint + config_snapshot）→ load → 4 字段在 DB 中位置正确（state/state_json、checkpoint/checkpoints 表、config/config_json）|
| 3 | `session_status_3_variants_persist` | Active / Completed / Interrupted 三 variant 各自 save+load → 值往返一致 |
| 4 | `current_round_some_vs_none_persist` | current_round = Some(5) 与 None 两种 → load 后 Option 状态保留 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub struct SessionMeta\|pub struct SessionData" crates/arf-session/src/lib.rs
grep -n "CREATE TABLE\|INSERT INTO\|state_json\|config_json" crates/arf-session/src/lib.rs
grep -n "sessions \.\.\. WHERE\|state_json\|config_json" crates/arf-session/src/lib.rs
```

逐行解释：
- `init_schema` 定义 `sessions` 表 10 列 + `checkpoints` 表 3 列
- `save` 写 10 列（meta 7 + state_json + config_json + state 走 serde_json::to_string）
- `load` 读 10 列 + checkpoints 表 latest
- 注意 `state.messages` 内的 tool_call_id / name 等嵌套字段也走 state_json

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group5-persist
cargo test -p arf-e2e --test session_serde_fields -- --nocapture --test-threads=1 2>&1 | tee /tmp/session_serde_fields_run.log
```

逐行解释：
- 4 test 应全过（直接 SqliteSessionStore API + in_memory + 字段断言）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/session_serde_fields_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：SessionMeta / SessionData 字段是 atomic 化的？无冗余字段？
- A2：state / config / checkpoint 序列化与 DB 列映射清晰？
- A3：SessionStatus / round_count 等字段值与 DB 表示（string 列 / INTEGER）转换在 single source？
- A4：serde 序列化 + DB 行转换是否集中？

**C. 输出**：`audit-probe-9.10.2.md`。
