# audit-probe-9.13.3：Tool Permission `Ask` 路径端到端探查

> Task 9.13.3 探查产出 — **Framework Engine 端 ToolPermission::Ask 路径是否实现？**
> 父 task doc：`docs/v1.x/phase9/task-9.13.3.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.x（tool integration）
> **本 task 探查：Engine 真实路径对 `ToolPermission::Ask` 工具的处理**

---

## §A 探查环境

- working tree：HEAD `cdd92f1`（task 9.13.2）+ uncommitted `crates/arf-e2e/tests/tool_permission_ask.rs`
- 测试文件：`crates/arf-e2e/tests/tool_permission_ask.rs`（4 test cases）
- 驱动：E2EHarness + ScriptedProvider + 写 echo tool + 端到端跑
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test tool_permission_ask -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.03s`**
- 关键运行输出：
  ```
  test ask_tool_runs_without_prompt ... [test1] Ask tool 端到端跑 OK (framework 不拦截) ✓
  test tool_permission_enum_traits ... [test2] arf_core::ToolSpec (Engine 用) 不含 permission 字段
  test engine_has_no_tools_field_in_config ... [test3] ⚠ 无 tools: Vec<ToolSpec> 字段
  test arf_agent_tools_with_ask_construct ... [test4] F-012 实证: ToolPermission 在 Engine 端 dead code
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/tool_permission_ask.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：Ask tool 端到端跑

```
单元              : ask_tool_end_to_end × §2.5
能力等级           : F（FAIL — ToolPermission 路径未实现，tool 直接跑）
判定依据          : E2EHarness 写 1 个 tool.toml + ScriptedProvider 调 tool_call
                   + 期望 framework 拦截/询问 → 实际 framework **直接跑** tool
                   端到端 work，ToolPermission 完全不拦截
file:line         : crates/arf-engine/src/engine.rs:534-598 do_tool_turn
                   直接发 tool_exec，无 permission 检查
                   ✗ ToolPermission::Ask 路径未实现
```

### 单元 2：ToolPermission enum 边界

```
单元              : tool_permission_traits × §2.5
能力等级           : C（partial — enum 在 arf-agent 端存在，Engine 端不可达）
判定依据          : arf_core::ToolSpec (Engine 用) **不含** permission 字段
                   arf_agent::ToolSpec (arf-agent 用) 含 permission: ToolPermission
                   两套 ToolSpec 不互通
file:line         : crates/arf-core/src/tool.rs:9-17 ToolSpec 结构 (Engine 用)
                   crates/arf-agent/src/tool.rs:25-46 ToolSpec 结构 (arf-agent 用)
                   crates/arf-agent/src/tool.rs:10 ToolPermission enum
                   △ enum 在 arf-agent 端存在；Engine 不读
```

### 单元 3：Engine AgentConfig 无 tools 字段

```
单元              : engine_config_no_tools × §2.5
能力等级           : F（FAIL — Engine 端完全无 permission 字段）
判定依据          : arf_engine::AgentConfig fields: model, resources,
                   system_prompt_template, initial_memory, allowed_paths, engine
                   **无** tools: Vec<ToolSpec> 字段
file:line         : crates/arf-engine/src/config.rs:77-94 AgentConfig
                   ✗ Engine 不接受 tool permission 声明
```

### 单元 4：两套 AgentConfig 不互通

```
单元              : two_agent_configs × §2.5
能力等级           : F（FAIL）
判定依据          : arf_agent::AgentConfig.tools: Vec<ToolSpec> 存在
                   arf_engine::AgentConfig 无对应字段
                   Engine 实际用 arf_engine::AgentConfig → 永远不读 arf_agent 字段
file:line         : crates/arf-agent/src/config.rs:20-47 arf_agent::AgentConfig
                   crates/arf-engine/src/config.rs:77-94 arf_engine::AgentConfig
                   ✗ 两套配置不互通
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `ask_tool_end_to_end` | **F** | tool 直接跑，Ask 路径未实现 |
| `tool_permission_traits` | **C** | enum 在 arf-agent 端存在，Engine 端不可达 |
| `engine_config_no_tools` | **F** | Engine AgentConfig 无 tools 字段 |
| `two_agent_configs` | **F** | 两套 AgentConfig 不互通 |

---

## §D 病灶登记

**本 task 新增 1 个 F-lesion**：

### F-012 — Engine 端 ToolPermission 路径完全未实现

```
病灶 ID       : F-012
类别         : F（framework 缺 permission 路径 + 两套 AgentConfig 不互通）
Signal         : 缺 tool permission check（spec §1.1 L2 tool_permission 三态 Allow/Ask/Deny 全部未实现）
触发情景       : §2.5（tool permission）
首次登记       : audit-probe-9.13.3.md §D
状态           : OPEN
file:line      : 1) crates/arf-engine/src/config.rs:77-94
                   arf_engine::AgentConfig 无 `tools: Vec<ToolSpec>` 字段
                2) crates/arf-agent/src/config.rs:20-47
                   arf_agent::AgentConfig.tools: Vec<ToolSpec> 存在 (Engine 不读)
                3) crates/arf-engine/src/engine.rs:534-598
                   do_tool_turn 直接发 tool_exec，**无 permission 检查**
                4) crates/arf-core/src/tool.rs:9-17
                   arf_core::ToolSpec (Engine 用的) 不含 permission 字段
                5) crates/arf-agent/src/tool.rs:10, 25-46
                   arf_agent::ToolSpec 含 permission: ToolPermission (Engine 不用)
                实证 test1: Ask tool 端到端跑 — framework 不拦截
                实证 test2: arf_core::ToolSpec 不含 permission
                实证 test3: arf_engine::AgentConfig 无 tools 字段
                实证 test4: 两套 AgentConfig 不互通
命中形态       : **L2 tool_permission 全部三态 (Allow/Ask/Deny) dead code**
                - capability-matrix §1.1 L2 列 `tool_permission（Allow / Ask / Deny）`
                - arf-agent 提供 `pub enum ToolPermission` (agent/tool.rs:10) +
                  `pub struct ToolSpec { ..., permission: ToolPermission }` (agent/tool.rs:30)
                - arf-agent 提供 `AgentConfig.tools: Vec<ToolSpec>` (agent/config.rs:31)
                - **Engine 用 arf_engine::AgentConfig, 不含 tools 字段**
                - **Engine 用 arf_core::ToolSpec (function-calling 用), 不含 permission 字段**
                - 后果：
                  1) 任何 ToolSpec.permission 配置被 ignore
                  2) 端到端 tool 调用全 Allow (无 Deny / Ask 拦截)
                  3) capability-matrix L2 的 `tool_permission` 完全 dead
                  4) 两套 AgentConfig / ToolSpec 不互通是根本原因
影响面         : 1) production tool 风险：dangerous tool (file write / shell exec)
                   无法在 framework 层 Deny——只能靠 app 端 model 训练
                2) Ask 路径无 framework hook——app 端实现 ask UI 必须
                   绕 framework (订阅 bus 拦截 tool_exec 自己处理)
                3) 两套 AgentConfig (arf_agent::AgentConfig vs arf_engine::AgentConfig)
                   不互通——app 端填 arf_agent::AgentConfig.tools 后传给 Engine
                   完全被 ignore
                4) 与 F-010 / F-011 同类（扩展点 declared but unwired）
修复方向       : 方案 A（最直接）：arf_engine::AgentConfig 加 `tools: Vec<arf_agent::ToolSpec>` 字段
（供参考）      + Engine do_tool_turn 前 check permission:
                  - Allow: 直接发 tool_exec
                  - Ask: 发 tool_permission_request → 等 tool_permission_response
                  - Deny: 直接 fail with tool permission denied
                方案 B（统一两套 AgentConfig）：废弃 arf_agent::AgentConfig，
                  Engine 改读 arf_agent::AgentConfig（含 tools）
                方案 C（最小改动）：Engine 接受 `Option<Arc<dyn ToolPermissionChecker>>`
                  trait，app 可选实现；默认实现是 Allow（降级行为）
                建议 A（最完整 + 与 capability-matrix 对齐）。
Engine 层蔓延  : N/A（engine 自身就是病灶所在层）
复现命令       : grep -n 'ToolPermission\|permission' crates/arf-engine/src/
                # 仅 comment + test 提到，无 runtime 检查
                grep -n 'tools:' crates/arf-engine/src/config.rs
                # 仅 in docs, 无 runtime 字段
                cargo test -p arf-e2e --test tool_permission_ask -- --nocapture --test-threads=1
                # [test1] Ask tool 端到端跑 OK (framework 不拦截) ✓
```

---

## §E 探查回归

- 9.5.1（mcp_fs_discovery）4 test pass，未受本 task 影响
- 9.13.3 新增 4 test pass
- 综合：9.13.3 = 4 test，**4 pass, 1 新 F-lesion (F-012)**
- F-001~F-011 + F-012 关联本 task
- **F-012 是 phase 9 第 12 个 F-lesion**：
  Engine 缺 tool permission 完整路径 + 两套 AgentConfig 不互通

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 Ask tool 端到端 test | ✓ test1 pass (tool 直接跑，无拦截) |
| 1 个 ToolPermission enum 边界 test | ✓ test2 pass (两套 ToolSpec 不互通) |
| 1 个 Engine AgentConfig 无 tools test | ✓ test3 pass (compile-time + runtime 验证) |
| 1 个两套 AgentConfig 不互通 test | ✓ test4 pass (F-012 证据) |
| 预期 0 新 F-lesion | ✗ **1 新 F-lesion (F-012)** |

> 结论：9.13.3 探查发现 framework **ToolPermission 路径完全未实现**：
> 1) `ToolPermission` enum 仅在 `arf-agent` 端存在
> 2) Engine 用 `arf_engine::AgentConfig`（无 `tools` 字段）和 `arf_core::ToolSpec`（无 `permission` 字段）
> 3) 两套 AgentConfig / ToolSpec 不互通
> 4) 端到端 tool 调用全 Allow，无 Ask / Deny 拦截
> 5) capability-matrix L2 `tool_permission` 完全 dead code。F-012 病灶已登记。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/tool_permission_ask.rs`（~160 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.13.3.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（F-012 在本 task audit-probe §D 首次登记，未追加到 lesion-registry.md，遵循任务约束）
- 待 commit + push
