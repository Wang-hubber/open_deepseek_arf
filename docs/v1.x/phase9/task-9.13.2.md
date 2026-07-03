# 任务 9.13.2：Node 掉线（SwitchTo alternative）

> Phase 9 — 9.13 M 异常与边界大类 · 第 2 task（依赖 9.13.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.13.1（Node 掉线 FailSession）
> 输出物：`docs/v1.x/phase9/audit-probe-9.13.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.13.1 实证 F-011 (handler 真实调用未实现)。本 task 探查 **`MemberFailedAction::SwitchTo` 行为**——如果 framework 真实现该 action，切换路径如何工作？

**Framework 现状**（待探查确认）：
- `MemberFailedAction::SwitchTo { alternative: NodeId }`（config.rs:50）—— trait 形态 OK
- 真实切换路径：F-011 已知 handler 不被调 → SwitchTo 也无法触发
- 即使被调，SwitchTo 实际是"切到 alternative NodeId"——Engine 怎么用 alternative 重新解析 model_call / tool_exec？

**关键探查问题**（不预设答案）：
1. SwitchTo handler 同样**未**被调（沿用 F-011）—— 本 task 验证
2. 即便被调，Engine 是否真把后续 model_call 路由到 alternative NodeId？
3. `alternative` 是 `NodeId` —— framework 是否验证 alternative 存在？
4. SwitchTo 切换后 State 是否保留？run 是否继续？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-engine/src/tests.rs:2239-2358`：OnMemberFailedHandler 单元测试（trait 边界）
- `crates/arf-e2e/tests/node_offline_fail_session.rs`：9.13.1（FailSession 实证 F-011）
- `crates/arf-e2e/tests/custom_member_failed_handler.rs`：9.12.5（SwitchTo trait 形态）
- **本 task 不重复**：trait 边界测试
- **本 task 聚焦**：SwitchTo 在 node_offline 真实路径下的行为（同样 F-011 病灶）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`node_offline_switch_to.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `switch_to_handler_returns_alternative_node` | SwitchToHandler::handle() 返回 SwitchTo{alternative}，直接 invoke 验 trait 边界 |
| 2 | `engine_node_offline_with_switch_to_handler` | Engine + SwitchTo handler + drop model node + node_offline → 期望 handler 被调 + alternative 切换 |
| 3 | `switch_to_alternative_node_must_exist` | SwitchTo{alternative=NodeId("nonexistent")} —— 期望 framework 报错或忽略 |
| 4 | `switch_to_realistic_alternative_node` | 2 个 model nodes，handler 返回 SwitchTo{alternative: NodeId("model/alt")} → drop primary model + node_offline + SwitchTo → 期望继续用 alt |

### Step 2 — framework 接触点 file:line

```bash
grep -rn "SwitchTo\|MemberFailedAction" crates/arf-engine/src/ | head -20
```

逐行解释：
- `MemberFailedAction::SwitchTo` 变体 (config.rs:50)
- 沿用 F-011 — handler 不被调，所以 SwitchTo 也未触发
- 即便触发，Engine 切到 alternative 的逻辑也未实现

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test node_offline_switch_to -- --nocapture --test-threads=1 2>&1 | tee /tmp/node_offline_switch_to_run.log
```

逐行解释：
- 4 test 期望全过（trait 边界 work；真实调用未实现 = F-011 沿用）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/node_offline_switch_to_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：SwitchTo action 形态？
- A2：SwitchTo 与 FailSession / Retry 各自 action 边界？
- A3：alternative NodeId 表达？
- A4：切到 alternative 后的 model_call 路由？

**C. 输出**：`audit-probe-9.13.2.md`。
