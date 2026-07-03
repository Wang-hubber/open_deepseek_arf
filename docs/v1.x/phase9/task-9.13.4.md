# 任务 9.13.4：Tool Permission `Deny` 路径

> Phase 9 — 9.13 M 异常与边界大类 · 第 4 task（依赖 9.5.x）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.x（tool integration）, 9.13.3（ToolPermission::Ask 探查）
> 输出物：`docs/v1.x/phase9/audit-probe-9.13.4.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

`ToolPermission::Deny` 在 `arf-agent::ToolSpec` 声明——理论上 Engine 调 tool 前应拒绝执行。本 task 探查 **Engine 端 `Deny` 路径真实实现**。

`Deny` 与 `Ask` 的关键区别：
- `Ask` 路径触发时 Engine 应发问 / 收答（需要 UI hook）
- `Deny` 路径触发时 Engine 应**直接拒绝 tool_exec 发送**，不调 tool
- `Deny` 是更简单的实现（不需要 round-trip），但 framework 同样可能完全未实现

**Framework 现状**（待探查确认）：
- `arf_agent::ToolSpec.permission: ToolPermission` 字段（agent/tool.rs:30）
- `arf_engine::AgentConfig` **无** `tools` 字段（engine/config.rs:77+）—— 沿用 F-010 / F-012
- Engine `do_tool_turn` 直接发 tool_exec（engine/engine.rs:536-598），无 permission 检查
- 没有 `tool_permission_denied` msg_type 或类似信号

**关键探查问题**（不预设答案）：
1. Engine 是否在 tool_call 前检查 `cfg.tools[].permission == Deny` 并拒绝？
2. `Deny` 路径触发时，Engine 是否发 `tool_permission_denied` 信号？
3. 端到端跑 Deny 工具——tool 是被拦截，还是照常执行？
4. capability-matrix §1.1 L2 `tool_permission.Deny` 死代码还是真实现？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-agent/src/tool.rs:48-227`：ToolPermission / ToolSpec 单元测试（含 Deny 变体）
- `crates/arf-agent/src/config.rs`：AgentConfig 含 `tools: Vec<ToolSpec>` 但仅 serde 验证
- `crates/arf-e2e/tests/tool_permission_ask.rs`：探查 `Ask` 路径（9.13.3）
- **本 task 不重复**：基本 enum 单元测试
- **本 task 聚焦**：Engine 真实路径对 `Deny` 工具的处理

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`tool_permission_deny.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `deny_tool_end_to_end` | ToolPermission::Deny 工具 + Engine 跑 + tool_call 发出 → 期望 behavior |
| 2 | `tool_permission_no_denied_msgtype` | 监听 bus，搜 `tool_permission_denied` 等 msg_type → 期望未发现 |
| 3 | `tool_permission_enum_traits_deny` | 直接 unit test enum (Deny 变体) + serde 端到端 OK |
| 4 | `engine_runs_deny_tool_directly` | 验 Engine 端 Deny 工具照样执行（无 framework 拦截） |

### Step 2 — framework 接触点 file:line

```bash
grep -rn "ToolPermission\|tool_permission\|tool_permission_denied" crates/ | head -20
grep -n "tools:" crates/arf-engine/src/config.rs
```

逐行解释：
- `ToolPermission::Deny` enum 变体声明在 arf-agent（agent/tool.rs:16）
- `ToolSpec.permission` 字段（agent/tool.rs:30）
- `arf_agent::AgentConfig.tools: Vec<ToolSpec>`（agent/config.rs:31）
- arf_engine::AgentConfig **无** tools 字段（engine/config.rs:77-94）
- engine/engine.rs:536-598 do_tool_turn 直接发 tool_exec，无 permission 检查

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test tool_permission_deny -- --nocapture --test-threads=1 2>&1 | tee /tmp/tool_permission_deny_run.log
```

逐行解释：
- 4 test 期望全过
- 任何 F-lesion 在 audit-probe §D 记录
- 预期沿用 F-012（Engine 端 ToolPermission 路径完全未实现）

**Read `/tmp/tool_permission_deny_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：ToolPermission enum 单一职责（声明 permission）？
- A2：ToolPermission 与 ToolSpec / EngineConfig 正交？
- A3：permission 值集中？
- A4：permission 检查集中？

**C. 输出**：`audit-probe-9.13.4.md`。
