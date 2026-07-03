# 任务 9.12.4：自定义 CheckpointRule（every_n_rounds / when_context_over）

> Phase 9 — 9.12 L 扩展点实现大类 · 第 4 task（依赖 9.2.3）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.3（Engine + 5 Checkpoint + 自定义 Rule）
> 输出物：`docs/v1.x/phase9/audit-probe-9.12.4.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.2.3 探查了 CheckpointRule 系统整体（5 位置 + 自定义 Rule）。本 task 深入 **app 端用 framework 提供 factory 构造 CheckpointRule**——`every_n_rounds` / `when_context_over` 两个 built-in factory。

**Framework 现状**（待探查确认）：
- `CheckpointRule::new(name, trigger, when, build)`（`crates/arf-core/src/checkpoint.rs:43-54`）—— 完整自定义
- `CheckpointRule::every_n_rounds(name, trigger, every_n, build)`（`checkpoint.rs:58-73`）—— built-in factory
- `CheckpointRule::when_context_over(name, trigger, ratio, build)`（`checkpoint.rs:77-92`）—— built-in factory
- `Checkpoint::BeforeModelCall` / `AfterModelCall` / `BeforeToolExec` / `AfterToolExec` / `RoundEnd`（5 个）

**关键探查问题**（不预设答案）：
1. `every_n_rounds` factory — 多次 `run_react` 后 round_count 累计 → 第 N 次 fire？边界（N=1, N=2）？
2. `when_context_over` factory — `state.context_tokens / model_context_window >= ratio` 时 fire？边界（= / > / 0 / 1）？
3. 5 个 Checkpoint 位置分别与 factory 组合 — 哪些位置 work？哪些不？
4. 同一 factory 在同一 trigger 多次注册 — 行为正确？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-core/src/lib.rs:1146-1180`：`CheckpointRule` 单元测试（3 tests）
- `crates/arf-e2e/tests/checkpoint_rules.rs`：9.2.3 端到端 probe（6 tests）
  - ckpt_every_n_rounds_builtin（已存在，但只测 1 次）
  - ckpt_when_context_over_builtin（已存在）
- **本 task 不重复**：基本 fire / 不 fire
- **本 task 聚焦**：边界（every_n=1 / N / 边界 ratio）+ 5 trigger × 2 factory 矩阵

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`custom_checkpoint_factory.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `every_n_rounds_boundary_1_2_3` | `every_n_rounds(1)` / `(2)` / `(3)` 边界 — round 1-N fire 行为 |
| 2 | `when_context_over_boundary_ratio_0_1` | `when_context_over(0.0)` / `(0.5)` / `(1.0)` 边界 — utilization = / > / < 阈值时 fire 行为 |
| 3 | `factory_x_5_checkpoint_matrix` | factory 组合 5 trigger — 每个 trigger 与 factory 都能正确 fire |
| 4 | `factory_build_returns_custom_message` | factory + build 返回自定义 ActionMessage（不是 MarkerMsg）—— 端到端 fire 后 message 类型正确 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub fn every_n_rounds\|pub fn when_context_over" crates/arf-core/src/checkpoint.rs
grep -n "pub fn new" crates/arf-core/src/checkpoint.rs
grep -n "context_utilization\|round_count" crates/arf-core/src/state.rs | head -10
```

逐行解释：
- `every_n_rounds` factory 把 `when` 闭包设为 `s.over_view.round_count > 0 && s.over_view.round_count as u32 % every_n == 0`（checkpoint.rs:69-71）
- `when_context_over` factory 把 `when` 设为 `s.over_view.context_utilization() >= ratio`（checkpoint.rs:89）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test custom_checkpoint_factory -- --nocapture --test-threads=1 2>&1 | tee /tmp/custom_checkpoint_factory_run.log
```

逐行解释：
- 4 test 应全过
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/custom_checkpoint_factory_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：CheckpointRule factory 是否单一职责（fire 条件 + build 消息）？
- A2：factory 与 new() 入口正交？
- A3：Checkpoint enum 5 变体集中？
- A4：fire 决策与 message 构建集中？

**C. 输出**：`audit-probe-9.12.4.md`。
