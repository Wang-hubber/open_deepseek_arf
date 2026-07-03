# 任务 9.9.7：3+ agent + peer + subagent 同时

> Phase 9 — 9.9 G Multi-agent 拓扑 · 第 7 task（依赖 9.9.6 + 9.9.5）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.6（3 engine peer 全连通）+ 9.9.5（3 层 subagent）
> 输出物：`docs/v1.x/phase9/audit-probe-9.9.7.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.9.6 探查 3 engine peer 全连通。9.9.5 探查 3 层 subagent 嵌套。9.9.7 探查 **3 engine 同时支持 peer + subagent**——每个 engine 既能 peer 通信，也能委派 sub-agent 任务。

**Framework 现状**（沿 9.9.6 + 9.9.5）：
- `peer_message` / `peer_reply` / `subagent_delegate` / `subagent_result` 都是 ActionMessage
- `engine_response_types` 把 `peer_message` → `peer_reply`，`subagent_delegate` → `subagent_result`
- 同一 engine 可同时注册 peer + subagent handler
- F-010 + F-011 + F-012 沿用

**关键探查问题**：
1. 3 engine 上能否同时支持 peer + subagent（routes 含 peer_message + subagent_delegate）？
2. handler 同时处理 peer_message 和 subagent_delegate 不冲突？
3. engine filter 同时含 peer_reply + subagent_result？
4. 1 个 engine 既是 peer target 又是 subagent target？是否需要多 provider name（F-010 限制）？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-e2e/tests/multi_agent_peer_fully_connected.rs`：3 engine peer（9.9.6）
- `crates/arf-e2e/tests/nested_subagent_three_layer.rs`：3 层 subagent（9.9.5）
- **本 task 不重复**：纯 peer / 纯 subagent
- **本 task 聚焦**：3 engine 同时支持 peer + subagent

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`multi_agent_peer_and_subagent.rs`，2 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `three_engines_with_both_peer_and_subagent_routes` | 3 engine routes 同时含 peer_message + subagent_delegate，filter 同时含 peer_reply + subagent_result |
| 2 | `three_engines_peer_and_subagent_independent` | 3 engine 上 1 个 peer_message + 1 个 subagent_delegate 同时发，独立 handler 处理，6 个 reply 互不干扰 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "peer_message\|subagent_delegate" crates/arf-engine/src/engine.rs
```

逐行解释：
- `engine_response_types` (line 726) 同时处理 peer_message → peer_reply + subagent_delegate → subagent_result
- filter 累加多个 response type

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group4-collab
cargo test -p arf-e2e --test multi_agent_peer_and_subagent -- --nocapture --test-threads=1 2>&1 | tee /tmp/multi_agent_peer_and_subagent_run.log
```

逐行解释：
- 2 test 应跑通（peer + subagent 协议互不干扰）
- F-010 + F-011 + F-012 沿用（3 provider 名 + ~10 处 glue + 2 处 NodeId 注入）

**Read `/tmp/multi_agent_peer_and_subagent_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 2 个单元的判定。

按 §4 跑 signals：
- A1：peer + subagent 协议是否在同一 engine 共存？
- A2：两种协议 handler 是否独立？
- A3：filter 是否累加？
- A4：派发是否冲突？

**C. 输出**：`audit-probe-9.9.7.md`。
