# 任务 9.10.3：5 Checkpoint 各调 `snapshot` 行为一致性

> Phase 9 — 9.10 H 持久化大类 · 第 3 task（依赖 9.10.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.10.1（EngineBuilder + SqliteSessionStore 端到端）
> 输出物：`docs/v1.x/phase9/audit-probe-9.10.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.10.1 探查了端到端 load round-trip。**本 task (9.10.3) 探查 5 Checkpoint 位置（BeforeModelCall /
AfterModelCall / BeforeToolExec / AfterToolExec / RoundEnd）各调 `snapshot` 行为是否一致——**
`engine.rs:330` 在 `evaluate_and_dispatch` 顶部调 `snapshot_if_configured`，
**不区分** 5 个 Checkpoint。验证 5 位置都正确触发且行为一致。

**Framework 现状**（待探查确认）：
- `Engine::snapshot_if_configured`（engine.rs:190-206）：用 `tokio::spawn` 异步写
- `evaluate_and_dispatch`（engine.rs:319-360）顶部调 snapshot，不区分 5 位置
- 1 round 无 tool → 3 fires（BeforeModelCall / AfterModelCall / RoundEnd）
- 1 round 1 tool → 7 fires（**注意** tool 位置只 fire 1 次，因为 tool 批并发：BeforeToolExec + AfterToolExec 各 1 次，engine.rs:285-287 "per-tool checkpoint 不并发触发，app-level checkpoint 围绕整批触发"）

**关键探查问题**（不预设答案）：
1. 5 位置中实际 fire 哪些？checkpoint_rules 为空时也 fire snapshot？
2. fire 顺序是否与 code 注释一致（engine.rs:246-298）？
3. `load()` 取到的是**最后 1 次** snapshot（按 captured_at DESC LIMIT 1，session/lib.rs:305）还是**所有** snapshot？
4. multiple snapshot 后 state 是否被覆盖？即后一次 snapshot 的 state 覆盖前一次？
5. snapshot 路径强制 `status = 'interrupted'`（session/lib.rs:412）——多次 snapshot 后 status 保持 Interrupted？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-e2e/tests/checkpoint_rules.rs`（9.2.3）—— 探查 CheckpointRule 的 fire 顺序（用 vec 记录）
- `crates/arf-e2e/tests/session_persist.rs`（9.10.1）—— 探查 1 round 端到端
- **本 task 不重复**：CheckpointRule 的 fire 顺序、save/load round-trip
- **本 task 聚焦**：5 位置各自 snapshot 一致性 + load 取 last（**不**保留 history）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

`session_checkpoint_5pos.rs`，3 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `snapshot_fires_at_3_positions_no_tool` | 1 round 无 tool → checkpoints 表应写 3 条（BeforeModelCall / AfterModelCall / RoundEnd），load 取最后 = RoundEnd |
| 2 | `snapshot_fires_at_4_positions_with_tool` | 1 round 1 tool → 期望 4 fires（BeforeModelCall / AfterModelCall / BeforeToolExec / AfterToolExec）+ 1 RoundEnd 实际是 turn 2，**但** 1 round 1 tool 共 7 fires（2 个 inner turn）— checkpoints 表应 ≥ 5 条 |
| 3 | `load_returns_latest_snapshot_only` | 多次 snapshot 后 load 取 captured_at DESC LIMIT 1 = 最新的；**不**保留 history |

### Step 2 — framework 接触点 file:line

```bash
grep -n "snapshot_if_configured\|evaluate_and_dispatch" crates/arf-engine/src/engine.rs
grep -n "INSERT.*checkpoints\|captured_at.*DESC LIMIT 1" crates/arf-session/src/lib.rs
```

逐行解释：
- `snapshot_if_configured` 单点 spawn（engine.rs:190-206），无 Checkpoint 类型参数化
- `evaluate_and_dispatch` 顶部无差别调 snapshot（engine.rs:330）
- `load` 读 checkpoints 表 latest（session/lib.rs:305）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group5-persist
cargo test -p arf-e2e --test session_checkpoint_5pos -- --nocapture --test-threads=1 2>&1 | tee /tmp/session_checkpoint_5pos_run.log
```

逐行解释：
- 3 test 应全过（mock + run_react + store.list / load）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/session_checkpoint_5pos_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 3 个单元的判定。

按 §4 跑 signals：
- A1：snapshot 写入路径单一接缝？
- A2：5 位置触发与 checkpoint_rules 关系是否清晰？
- A3：snapshot 状态（status 强制 Interrupted）单点声明？
- A4：snapshot 异步 spawn + 状态 UPDATE 集中？

**C. 输出**：`audit-probe-9.10.3.md`。
