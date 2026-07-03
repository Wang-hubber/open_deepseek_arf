# 任务 9.9.4：双 agent + subagent 嵌套（2 层）

> Phase 9 — 9.9 G Multi-agent 拓扑 · 第 4 task（依赖 9.9.3）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.3（1 层委派）
> 输出物：`docs/v1.x/phase9/audit-probe-9.9.4.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.9.3 探查 1 层委派（parent → child）。9.9.4 探查 2 层嵌套（parent → child → grandchild）。

**Framework 现状**（沿 9.9.3）：
- SubagentDelegate 是纯数据协议，handler 派发需 app 桥接（F-011）
- 2 层嵌套 = 2 个 SubagentDelegate + 2 个 SubagentResult 链
- correlation_id 须端到端匹配

**关键探查问题**：
1. 2 层 SubagentDelegate 的 correlation_id 链能否逐层匹配？
2. 每一层的 handler 派发需 app 端都做 bus.subscribe + dispatch（沿 F-011 × 2）
3. grandchild 的 result 如何逐层回传到 parent（中间 child 需转发）？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-e2e/tests/dual_agent_subagent.rs`：1 层委派
- **本 task 不重复**：1 层路径
- **本 task 聚焦**：2 层 chain + 中间层转发

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`nested_subagent_two_layer.rs`，2 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `nested_two_layer_chain_constructed` | parent → child → grandchild 3 engine + 2 SubagentDelegate，验证链能搭 |
| 2 | `nested_two_layer_correlation_id_propagates` | 2 层委派 correlation_id 端到端匹配，grandchild 的 output 透传到 parent |

### Step 2 — framework 接触点 file:line

```bash
grep -n "correlation_id" crates/arf-core/src/message.rs
```

逐行解释：
- `SubagentDelegate.correlation_id: Uuid`（line 168）— 必填
- `SubagentResult.correlation_id: Uuid`（line 217）— 必填，与 delegate 对应

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group4-collab
cargo test -p arf-e2e --test nested_subagent_two_layer -- --nocapture --test-threads=1 2>&1 | tee /tmp/nested_subagent_two_layer_run.log
```

逐行解释：
- 2 test 应跑通（多层 app 端 bridge）
- F-010 在 3 engine 同 bus 上更明显（每个 engine 需唯一 provider）

**Read `/tmp/nested_subagent_two_layer_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 2 个单元的判定。

按 §4 跑 signals：
- A1：SubagentDelegate 在多层下是否仍 atomic？
- A2：每层是否正交（独立 correlation_id）？
- A3：layer 间 ID 唯一性？
- A4：中间层转发是否集中？

**C. 输出**：`audit-probe-9.9.4.md`。
