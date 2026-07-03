# 任务 9.9.2：双 agent + peer（A2A / PeerMessage + PeerReply）

> Phase 9 — 9.9 G Multi-agent 拓扑 · 第 2 task（依赖 9.9.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.1（双 agent 独立）
> 输出物：`docs/v1.x/phase9/audit-probe-9.9.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.9.1 探查了双 agent 独立运行（无连接）。9.9.2 引入**跨 agent 通信**——`PeerMessage` + `PeerReply` A2A 协议。

**Framework 现状**（待探查确认）：
- `arf_core::message::PeerMessage` / `PeerReply` 已是标准 `ActionMessage`
- `engine_response_types()` 把 `peer_message` route key → `peer_reply`（白名单映射）
- Engine filter 仅收 response types（`model_response` / `tool_result` / `peer_reply`）
- Engine **不**主动 dispatch peer_message 到 MessageHandler（F-004 衍生）

**关键探查问题**（不预设答案）：
1. Engine filter 是否真的不含 `peer_message`？app 怎么让 engine 收 peer_message？
2. `PeerMessage::new(from_session, to_session, content)` 构造后通过 `engine.handle().send("peer_message", ...)` 能否送达？
3. `add_handler(Arc<dyn MessageHandler>)` 注册 `peer_message` handler 是否需要 `replace=true`？
4. handler 内部发 `peer_reply`，engine A 的 filter 能否收？（A 配 routes 后 filter 含 peer_reply）

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-e2e/tests/custom_handler.rs`：MessageHandler / ResponseProcessor 探查（不涉及 peer_message）
- `crates/arf-engine/src/dispatcher.rs`：HandlerRegistry 单元测试
- **本 task 不重复**：单 engine handler
- **本 task 聚焦**：双 engine A2A 通信 + framework 缺自动 dispatch 的发现

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`dual_agent_peer.rs`，3 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `peer_message_wired_via_external_subscriber` | 2 engine + 外部 bus.subscribe listener 收 peer_message，回 PeerReply |
| 2 | `peer_message_handler_registered_on_engine` | engine.add_handler(peer_message) + dispatch_incoming 手动触发 |
| 3 | `peer_round_trip_two_engines` | A↔B 双向往返，验证 session_id 正确路由 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "engine_response_types\|response_msg_type_for" crates/arf-engine/src/engine.rs
grep -n "pub.*add_handler\|pub.*dispatch_incoming" crates/arf-engine/src/engine.rs
```

逐行解释：
- `engine_response_types()` line 726-744：route keys → response types 映射
- `Engine::add_handler` line 139：注册 handler
- `Engine::dispatch_incoming` line 172：手动 dispatch（Engine 不自动触发）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group4-collab
cargo test -p arf-e2e --test dual_agent_peer -- --nocapture --test-threads=1 2>&1 | tee /tmp/dual_agent_peer_run.log
```

逐行解释：
- 3 test 应跑通（外部 subscriber 桥接 peer_message → engine）
- Engine filter 不收 peer_message 的 lesion 在 §D 记录

**Read `/tmp/dual_agent_peer_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 3 个单元的判定。

按 §4 跑 signals：
- A1：PeerMessage 协议是否 atomic？（from/to/content + correlation_id 4 字段）
- A2：sender/receiver engine 协议是否正交？
- A3：session_id 路由是否唯一？
- A4：peer 收发处理是否集中？（app 端需 subscriber + handler 桥接 = 处理分散）

**C. 输出**：`audit-probe-9.9.2.md`。
