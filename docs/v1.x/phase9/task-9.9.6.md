# 任务 9.9.6：3+ agent + peer 全连通

> Phase 9 — 9.9 G Multi-agent 拓扑 · 第 6 task（依赖 9.9.2）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.2（双 agent peer）
> 输出物：`docs/v1.x/phase9/audit-probe-9.9.6.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.9.2 探查 2 engine peer（A2A）。9.9.6 探查 3+ engine peer **全连通**——任意两个 engine 能互发 PeerMessage / PeerReply。

**Framework 现状**（沿 9.9.2）：
- PeerMessage / PeerReply 是 ActionMessage
- `engine_response_types` 把 `peer_message` → `peer_reply`
- handler 派发需 app 桥接（F-011）
- 3 engine 全连通 = 3×2 = 6 条定向 peer 边
- F-010 沿用（3 engine 需不同 provider）

**关键探查问题**：
1. 3 engine 同 bus 上，能组成全连通 peer 拓扑（A↔B, A↔C, B↔C）吗？
2. 每条 peer 边的 handler 派发需 app 端做 bus.subscribe + dispatch（沿 F-011 × 6）
3. 双向 peer reply 是否沿用 9.9.2 的 from-online-NodeId 限制（沿 F-012）？
4. 全连通下 correlation_id 路由是否正交？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-e2e/tests/dual_agent_peer.rs`：2 engine A↔B
- **本 task 不重复**：2 engine 路径
- **本 task 聚焦**：3+ engine 全连通拓扑 + 6 条 peer 边

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`multi_agent_peer_fully_connected.rs`，2 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `three_engines_fully_connected_peers` | 3 engine 全连通（6 条 peer 边），验证任意两点能 peer |
| 2 | `three_engines_bidirectional_peer_reply` | 3 engine 互发 peer_message，验证 6 个 peer_reply 全部回到 source |

### Step 2 — framework 接触点 file:line

```bash
grep -n "peer_message\|peer_reply" crates/arf-engine/src/engine.rs
grep -n "PeerMessage\|PeerReply" crates/arf-core/src/message.rs
```

逐行解释：
- `PeerMessage::new` (line 273) — from_session, to_session, content
- `PeerReply::new` (line 314) — correlation_id, content
- `engine_response_types` line 751 把 `peer_message` → `peer_reply`

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group4-collab
cargo test -p arf-e2e --test multi_agent_peer_fully_connected -- --nocapture --test-threads=1 2>&1 | tee /tmp/multi_agent_peer_fully_connected_run.log
```

逐行解释：
- 2 test 应跑通（3 engine + 6 peer 边，handler dispatch 需 ~6× glue）
- F-010 沿用（3 engine 需不同 provider 名）
- F-011 沿用（每个 engine handler 需手动 dispatch_incoming）
- F-012 沿用（peer_reply 的 from 必须是 online NodeId）

**Read `/tmp/multi_agent_peer_fully_connected_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 2 个单元的判定。

按 §4 跑 signals：
- A1：PeerMessage 跨 engine 是否 atomic？
- A2：每条 peer 边是否正交？
- A3：correlation_id 在 3 engine 全连通下是否唯一？
- A4：peer 派发是否集中？

**C. 输出**：`audit-probe-9.9.6.md`。
