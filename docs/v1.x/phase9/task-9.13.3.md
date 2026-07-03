# 任务 9.13.3：Tool Permission `Ask` 路径

> Phase 9 — 9.13 M 异常与边界大类 · 第 3 task（依赖 9.5.x）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.x（tool integration）
> 输出物：`docs/v1.x/phase9/audit-probe-9.13.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

`ToolPermission::Ask` 在 `arf-agent::ToolSpec` 声明——理论上 Engine 调 tool 前应问用户。本 task 探查 **Engine 端 `Ask` 路径真实实现**。

**Framework 现状**（待探查确认）：
- `arf_agent::ToolSpec.permission: ToolPermission` 字段（agent/tool.rs:30）
- `arf_engine::AgentConfig` **无** `tools` 字段（engine/config.rs:77+）—— 沿用 9.5.1 F-010 类似情况
- Engine 收到 `model_response.tool_calls[i].name` 直接执行，无 permission 检查

**关键探查问题**（不预设答案）：
1. Engine 是否在 tool_call 前检查 `cfg.tools[].permission`？
2. `Ask` 路径触发时，Engine 怎么发问 / 收答？是否有 `tool_permission_request` / `tool_permission_response` msg_type？
3. 如果 Ask 未实现，tool 自动执行（降级为 Allow 行为）？
4. `ToolPermission` enum 在 framework 端完全是 dead code？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-agent/src/tool.rs:48-227`：ToolPermission / ToolSpec 单元测试（仅 enum 构造 / serde）
- `crates/arf-agent/src/config.rs`：AgentConfig 含 `tools: Vec<ToolSpec>` 但仅 serde 验证
- **本 task 不重复**：基本 enum 单元测试
- **本 task 聚焦**：Engine 真实路径对 Ask 工具的处理

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`tool_permission_ask.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `ask_tool_runs_without_prompt` | ToolPermission::Ask 工具 + Engine 跑 + tool_call 发出 → 期望 behavior |
| 2 | `ask_tool_no_permission_request_msgtype` | 监听 bus，搜 `tool_permission_request` 等 msg_type → 期望未发现 |
| 3 | `tool_permission_enum_traits` | 直接 unit test enum (Allow / Ask / Deny) + serde 端到端 OK |
| 4 | `engine_has_no_tools_field_in_config` | 验 Engine 端 AgentConfig 无 `tools: Vec<ToolSpec>` 字段（compile-time + runtime reflection） |

### Step 2 — framework 接触点 file:line

```bash
grep -rn "ToolPermission\|tool_permission\|tool_permission_request" crates/ | head -20
grep -n "tools:" crates/arf-engine/src/config.rs
```

逐行解释：
- `ToolPermission` enum 声明在 arf-agent（agent/tool.rs:10）
- `ToolSpec.permission` 字段（agent/tool.rs:30）
- `arf_agent::AgentConfig.tools: Vec<ToolSpec>`（agent/config.rs:31）
- arf_engine::AgentConfig **无** tools 字段（engine/config.rs:77-94）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test tool_permission_ask -- --nocapture --test-threads=1 2>&1 | tee /tmp/tool_permission_ask_run.log
```

逐行解释：
- 4 test 期望全过
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/tool_permission_ask_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：ToolPermission enum 单一职责（声明 permission）？
- A2：ToolPermission 与 ToolSpec / EngineConfig 正交？
- A3：permission 值集中？
- A4：permission 检查集中？

**C. 输出**：`audit-probe-9.13.3.md`。
