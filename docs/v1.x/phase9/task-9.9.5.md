# 任务 9.9.5：3+ agent + subagent 嵌套（3 层）

> Phase 9 — 9.9 G Multi-agent 拓扑 · 第 5 task（依赖 9.9.4）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.4（2 层委派）
> 输出物：`docs/v1.x/phase9/audit-probe-9.9.5.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.9.4 探查 2 层嵌套（parent → child → grandchild）。9.9.5 探查 3 层嵌套（parent → child → grandchild → great-grandchild）。

**Framework 现状**（沿 9.9.4）：
- 2 层嵌套 = 2 个 SubagentDelegate + 1 个 SubagentResult（grandchild → child）
- 3 层嵌套 = 3 个 SubagentDelegate + 2 个 SubagentResult（great-grandchild → grandchild → child）
- 每多一层：1 个 ForwardHandler（中间层）+ 1 个 LeafHandler（终层）
- correlation_id 须端到端匹配（4 个 engine 共享 1 个 cid_root）
- F-010 + F-011 + F-012 沿用

**关键探查问题**：
1. 3 层 SubagentDelegate 链能否逐层匹配（parent → child → grandchild → great-grandchild）？
2. 2 个中间层（child + grandchild）的转发能否保持 correlation_id？
3. 终层（great-grandchild）的 result 能否逐层回传到 parent（中间 2 层需转发）？
4. F-012（bus.send to 节点必须 online）在 3 层嵌套中是否更突出？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-e2e/tests/dual_agent_subagent.rs`：1 层委派
- `crates/arf-e2e/tests/nested_subagent_two_layer.rs`：2 层委派
- **本 task 不重复**：1 层 / 2 层路径
- **本 task 聚焦**：3 层 chain + 2 个中间层转发 + 终层 result 回传中间层

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`nested_subagent_three_layer.rs`，2 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `nested_three_layer_chain_constructed` | 4 engine + 3 SubagentDelegate，验证链能搭 |
| 2 | `nested_three_layer_correlation_id_propagates` | 3 层委派 correlation_id 端到端匹配，great-grandchild output 透传 grandchild |

### Step 2 — framework 接触点 file:line

```bash
grep -n "correlation_id" crates/arf-core/src/message.rs
```

逐行解释：
- `SubagentDelegate.correlation_id: Uuid`（line 168）— 必填
- `SubagentResult.correlation_id: Uuid`（line 217）— 必填，与 delegate 对应
- 3 层 = 3 个 SubagentDelegate，correlation_id 必须全 = root cid

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group4-collab
cargo test -p arf-e2e --test nested_subagent_three_layer -- --nocapture --test-threads=1 2>&1 | tee /tmp/nested_subagent_three_layer_run.log
```

逐行解释：
- 2 test 应跑通（3 层 app 端 bridge，每个中间层 ~25 行 glue）
- F-012 在 3 层嵌套中更突出（4 engine 每个需真实 online NodeId，handler 需更多 hardcode）

**Read `/tmp/nested_subagent_three_layer_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 2 个单元的判定。

按 §4 跑 signals：
- A1：SubagentDelegate 在 3 层下是否仍 atomic？
- A2：每层是否正交（独立 correlation_id 但端到端统一）？
- A3：layer 间 ID 唯一性？
- A4：中间层转发是否集中？

**C. 输出**：`audit-probe-9.9.5.md`。
