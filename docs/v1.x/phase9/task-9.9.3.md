# 任务 9.9.3：双 agent + subagent 委派（1 层）

> Phase 9 — 9.9 G Multi-agent 拓扑 · 第 3 task（依赖 9.9.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.1（双 agent 独立）
> 输出物：`docs/v1.x/phase9/audit-probe-9.9.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.9.2 探查了 peer (engine ↔ engine)。9.9.3 探查 subagent 委派（parent → child engine，1 层）。

**Framework 现状**（待探查确认）：
- `arf_core::message::SubagentDelegate` / `SubagentResult` 标准 ActionMessage
- `engine_response_types` 把 `subagent_delegate` → `subagent_result`（白名单）
- Engine filter 仅 response types
- Engine **不**自动 dispatch subagent_delegate（沿 F-011）
- Engine **不**自动启动子 engine——subagent_node_id 是普通 NodeId 字段

**关键探查问题**：
1. `SubagentDelegate::new(parent_session, subagent_node_id, task)` 构造后能否发到目标 subagent engine？
2. 目标 subagent engine 收到后能调用 handler 吗？
3. handler 调子 engine.run(...) 后构造 `SubagentResult.success(correlation_id, output)` 回 parent？
4. parent engine filter 含 subagent_result 吗？（routes 含 subagent_delegate）

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-e2e/tests/dual_agent_peer.rs`：peer (A↔B) 端到端
- `crates/arf-core/src/lib.rs:1752-1803`：SubagentDelegate 单元测试
- **本 task 不重复**：纯类型测试
- **本 task 聚焦**：parent engine 发 subagent_delegate → child engine 收 + 委派 work + 回 SubagentResult

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`dual_agent_subagent.rs`，3 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `subagent_delegate_constructed_and_sent` | parent engine 发 SubagentDelegate，bus 上定向送达 child |
| 2 | `subagent_handler_runs_child_engine` | child engine handler 调子 engine.run(...) 拿到 output，构造 SubagentResult 回 parent |
| 3 | `subagent_result_received_by_parent` | parent engine filter 含 subagent_result，bus.subscribe 可见 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "subagent_delegate\|subagent_result" crates/arf-engine/src/engine.rs
grep -n "subagent" crates/arf-core/src/message.rs
```

逐行解释：
- `engine_response_types` line 751：`subagent_delegate` → `subagent_result`
- `SubagentDelegate::new(line 176)` 构造
- `SubagentResult::success(line 230)` 构造

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group4-collab
cargo test -p arf-e2e --test dual_agent_subagent -- --nocapture --test-threads=1 2>&1 | tee /tmp/dual_agent_subagent_run.log
```

逐行解释：
- 3 test 应跑通（child engine handler 跑自己 chat，构造 SubagentResult）
- F-011 沿用（Engine 不自动 dispatch）；child engine 需手动 dispatch_incoming

**Read `/tmp/dual_agent_subagent_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 3 个单元的判定。

按 §4 跑 signals：
- A1：SubagentDelegate 是否 atomic？（parent_session + subagent_node_id + task + context 4 字段）
- A2：parent/child 协议是否正交？
- A3：correlation_id 路由是否唯一？
- A4：child engine run + SubagentResult 回传是否集中？

**C. 输出**：`audit-probe-9.9.3.md`。
