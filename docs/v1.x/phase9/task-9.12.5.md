# 任务 9.12.5：自定义 OnMemberFailedHandler（FailSession / SwitchTo）

> Phase 9 — 9.12 L 扩展点实现大类 · 第 5 task（依赖 9.13.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.13.1（Node 掉线 FailSession — 实际触发 OnMemberFailedHandler 路径）
> 输出物：`docs/v1.x/phase9/audit-probe-9.12.5.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.13.1 探查 Node 掉线时 `OnMemberFailedAction::FailSession` 的 framework 行为。本 task 探查 **app 端实现自定义 `OnMemberFailedHandler` trait**——capability matrix L8 扩展点。

**Framework 现状**（待探查确认）：
- `MemberFailedAction` enum（`crates/arf-engine/src/config.rs:47-55`）—— 3 变体（FailSession / Retry{delay_ms} / SwitchTo{alternative}）
- `OnMemberFailedHandler` trait（`config.rs:57-59`）—— `handle(&NodeId, &NodeId, &str) -> MemberFailedAction`
- `EngineConfig.on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>`（config.rs:25）
- 已对 `F: Fn(...) + Send + Sync` 自动 blanket impl（config.rs:61-68）—— app 可传闭包
- Engine 在 `node_offline` 时调用 `on_member_failed.handle(...)`—— 但具体调用点需要 9.13.1 实证

**关键探查问题**（不预设答案）：
1. app 用闭包 `|_a, _m, _r| MemberFailedAction::FailSession` 作为 handler —— 端到端 build + store OK？
2. app 用 struct 实现 `OnMemberFailedHandler` trait —— 端到端 work？
3. 三种 action（FailSession / Retry / SwitchTo）framework 真实处理路径？
4. handler 默认是 FailSession（config.rs:54）—— None 时与 explicit FailSession 行为是否一致？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-engine/src/tests.rs:2223-2358`：OnMemberFailedHandler 单元测试（8 tests）
- `crates/arf-engine/tests/integration.rs:260-269`：`e2e_on_member_failed_handler_stored_in_config` 单元级
- `crates/arf-e2e/tests/bus_exceptions.rs`：9.1.5 探查 node_offline 行为
- **本 task 不重复**：基本 trait 构造测试
- **本 task 聚焦**：自定义 OnMemberFailedHandler 三种 action (FailSession / Retry / SwitchTo) + 端到端 build 验

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`custom_member_failed_handler.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|
| 1 | `handler_closure_fail_session` | 闭包 handler 返回 FailSession —— 端到端 build + 验证 cfg.on_member_failed = Some |
| 2 | `handler_struct_three_actions` | struct impl OnMemberFailedHandler trait，handle() 根据 member 节点名返回不同 action（FailSession / Retry / SwitchTo）—— 端到端 build OK |
| 3 | `handler_default_none_is_fail_session` | 不设 handler (None) —— engine 仍 OK build（验证 None 是合法状态） |
| 4 | `handler_invoke_directly_returns_action` | 直接调 `handler.handle(&agent, &member, "reason")` 端到端 work，无需触发 node_offline 真实链路 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub enum MemberFailedAction\|pub trait OnMemberFailedHandler" crates/arf-engine/src/config.rs
grep -n "on_member_failed\|MemberFailedAction" crates/arf-engine/src/engine.rs
```

逐行解释：
- `MemberFailedAction` 3 变体
- `OnMemberFailedHandler` trait + blanket impl 闭包
- `EngineConfig.on_member_failed` 字段
- 实际调用点（待 9.13.1 实证，本 task 不深探）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test custom_member_failed_handler -- --nocapture --test-threads=1 2>&1 | tee /tmp/custom_member_failed_handler_run.log
```

逐行解释：
- 4 test 应全过
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/custom_member_failed_handler_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：OnMemberFailedHandler 是否一个职责（处理 node 失败）？
- A2：OnMemberFailedHandler 与 ResponseProcessor / CheckpointRule 等扩展点正交？
- A3：MemberFailedAction enum 集中？
- A4：node 失败处理集中？

**C. 输出**：`audit-probe-9.12.5.md`。
