# 任务 9.13.1：Node 掉线（OnMemberFailedAction::FailSession）

> Phase 9 — 9.13 M 异常与边界大类 · 第 1 task（依赖 9.1.5）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.1.5（Bus 异常 lagged / 掉线 / 重连）
> 输出物：`docs/v1.x/phase9/audit-probe-9.13.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.1.5 探查了 Bus 侧的 node_offline 触发（drop handle → heartbeat timeout）。本 task 深入 **Engine 端 node_offline 触发 `OnMemberFailedHandler::handle()` 的真实路径**——`FailSession` 行为。

**Framework 现状**（待探查确认）：
- `EngineConfig.on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>`（config.rs:25）
- `MemberFailedAction::FailSession` 变体（config.rs:48）
- Engine 在 `node_offline` 时的实际处理路径（engine.rs:81-92）—— **lifecycle listener 只 invalidate cache, handler invocation 待 6.x 留**
- 集成测试 `e2e_on_member_failed_handler_stored_in_config` 仅验证"build 接受"（integration.rs:260-269）

**关键探查问题**（不预设答案）：
1. 注册 `on_member_failed = Some(handler)` —— node_offline 时 handler 真的被调？
2. handler 返回 `FailSession` —— engine run 会停止？返回 `RunError::SessionFailed`？
3. node_offline 真实链路（drop node handle → heartbeat timeout → node_offline broadcast → engine subscribe → handler call）端到端 work？
4. lifecycle listener 当前只 invalidate cache——**handler 实际调用点缺失**是 F-lesion？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-engine/src/tests.rs:2239-2358`：OnMemberFailedHandler 单元测试（仅 trait 边界，无真实 node_offline 触发）
- `crates/arf-engine/tests/integration.rs:260-269`：`e2e_on_member_failed_handler_stored_in_config`（仅 build 接受）
- `crates/arf-e2e/tests/bus_exceptions.rs`：9.1.5 node_offline 基础（drop handle + heartbeat timeout）
- `crates/arf-e2e/tests/custom_member_failed_handler.rs`：9.12.5 trait + 3 actions + build
- **本 task 不重复**：trait 边界 + build
- **本 task 聚焦**：node_offline 真实链路触发 handler 端到端

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`node_offline_fail_session.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `node_offline_triggers_handler_fail_session` | 注册 handler + 跑 model call + drop model node handle → 等 heartbeat timeout → 期望 handler 被调 + run 返回 SessionFailed |
| 2 | `node_offline_no_handler_uses_default_fail` | 不注册 handler + drop model node + 跑 → 期望 framework 走 default FailSession (或 silent) |
| 3 | `bus_only_node_offline_no_engine` | 验证 Bus 单独 node_offline 行为（baseline，与 9.1.5 关联） |
| 4 | `handler_invocation_count_after_offline` | handler 多次 offline 调，count 累加 |

### Step 2 — framework 接触点 file:line

```bash
grep -rn "on_member_failed\|MemberFailed" crates/arf-engine/src/ | head -20
grep -n "node_offline\|node_online" crates/arf-engine/src/engine.rs | head -5
```

逐行解释：
- `EngineConfig.on_member_failed` 字段 (config.rs:25)
- `engine.rs:81-92` lifecycle listener — **只 invalidate cache, 不调 handler**
- 这是 framework 实现缺口（沿用 6.8 task 注释）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test node_offline_fail_session -- --nocapture --test-threads=1 2>&1 | tee /tmp/node_offline_fail_session_run.log
```

逐行解释：
- 4 test 应大部分 fail（handler 真实调用未实现）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/node_offline_fail_session_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：handler 是否单一职责（处理 node 失败）？
- A2：handler 调用点是否唯一？
- A3：node_offline 处理路径集中？
- A4：fail 行为集中？

**C. 输出**：`audit-probe-9.13.1.md`。
