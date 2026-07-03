# 任务 9.11.2：`when_context_over` CheckpointRule 触发

> Phase 9 — 9.11 I 压缩大类 · 第 2 task（依赖 9.11.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.11.1（Compactor + 默认 Summarizer 端到端）
> 输出物：`docs/v1.x/phase9/audit-probe-9.11.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.11.1 探查了 Compactor 端到端。**本 task (9.11.2) 探查 `when_context_over` CheckpointRule 端到端——**
app 通过 `CheckpointRule::when_context_over(0.7, build_closure)` + `arf_compactor::when_context_over(0.7, keep_tail)`
注册 rule，Engine 在 BeforeModelCall 触发时调 build_closure 返回 `CompactRequest`，
App 端 handler 收到 CompactRequest 后调 `Compactor::compact(state, keep_tail)` 端到端压缩。

**Framework 现状**（待探查确认）：
- `CheckpointRule::when_context_over(name, trigger, ratio, build)` (core/checkpoint.rs:77-92)
- `arf_compactor::when_context_over(ratio, keep_tail) -> CheckpointRule` (compactor/lib.rs:157-169)
  - 内部 create CheckpointRule with trigger = BeforeModelCall
  - `when = state.context_utilization() >= ratio`
  - `build = |state| Box::new(CompactRequest { threshold, keep_tail })`
- `CompactRequest` (compactor/lib.rs:175-194) ActionMessage, msg_type = "compact_request", intent = Command
- `CompactDone` (compactor/lib.rs:197-215) informational marker

**关键探查问题**（不预设答案）：
1. `arf_compactor::when_context_over(0.7, 4)` 返回的 CheckpointRule 端到端 work？`when` / `build` 闭包正确？
2. state.utilization >= 0.7 时 fires → build 返回 CompactRequest { threshold, keep_tail }？
3. App 端 handler 收到 CompactRequest 后调 Compactor::compact → state.messages 压缩 → 真实压缩？
4. 边界：utilization < 0.7 → 不 fire？
5. 与 Engine 主循环集成——Engine 跑 1 round 触发 rule，build 返回的 CompactRequest 通过 engine dispatch 路径发送（"compact_request" msg_type）？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-compactor/src/lib.rs` 单测已测 when_context_over_builds_rule / when_context_over_does_not_fire_when_low
- `crates/arf-compactor/src/lib.rs` 单测已测 compact_request_basic / compact_request_serde_roundtrip
- `crates/arf-core/src/checkpoint.rs` 单测（既有）已测 when_context_over predicate
- **本 task 不重复**：单测字段 / predicate
- **本 task 聚焦**：端到端 probe——CheckpointRule + 真实 Engine + state mutation

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

`compact_checkpoint_rule.rs`，3 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `when_context_over_rule_fires_at_high_utilization` | state.context_tokens=80, model_context_window=100 → util=0.8 ≥ 0.7 → rule.fires(&state) == true |
| 2 | `when_context_over_rule_does_not_fire_at_low_utilization` | state.context_tokens=30, model_context_window=100 → util=0.3 < 0.7 → rule.fires(&state) == false |
| 3 | `when_context_over_builds_compact_request_with_correct_fields` | rule.build_msg(&state) → Box<dyn ActionMessage>；msg_type = "compact_request"；payload 含 threshold + keep_tail |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub fn when_context_over\|pub struct CompactRequest\|impl ActionMessage for CompactRequest" crates/arf-compactor/src/lib.rs crates/arf-core/src/checkpoint.rs
```

逐行解释：
- `arf_compactor::when_context_over` 是 helper factory，包装 `core::CheckpointRule::when_context_over`
- `CompactRequest` ActionMessage impl：msg_type = "compact_request", intent = Command
- App 必须 register route for "compact_request"（否则 UndeclaredMsgType 错误，engine/checkpoint.rs:140-144）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group5-persist
cargo test -p arf-e2e --test compact_checkpoint_rule -- --nocapture --test-threads=1 2>&1 | tee /tmp/compact_checkpoint_rule_run.log
```

逐行解释：
- 3 test 应全过（rule predicate + build msg）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/compact_checkpoint_rule_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 3 个单元的判定。

按 §4 跑 signals：
- A1：`when_context_over` rule 是 atomic（"fire when over threshold + build CompactRequest"）？
- A2：与 CheckpointRule trait 正交（独立 helper factory）？
- A3：threshold + keep_tail 字段单点声明？
- A4：predicate 计算 utilization 在单点（core/checkpoint.rs:89 `state.over_view.context_utilization() >= ratio`）？

**C. 输出**：`audit-probe-9.11.2.md`。
