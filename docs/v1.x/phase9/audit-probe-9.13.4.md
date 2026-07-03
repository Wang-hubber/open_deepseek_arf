# audit-probe-9.13.4：Tool Permission `Deny` 路径端到端探查

> Task 9.13.4 探查产出 — **Framework Engine 端 ToolPermission::Deny 路径是否实现？**
> 父 task doc：`docs/v1.x/phase9/task-9.13.4.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.x（tool integration）, 9.13.3（ToolPermission::Ask 探查，F-012 已证）
> **本 task 探查：Engine 真实路径对 `ToolPermission::Deny` 工具的处理**

---

## §A 探查环境

- working tree：HEAD `572edaf`（task 9.13.3）+ uncommitted `crates/arf-e2e/tests/tool_permission_deny.rs`
- 测试文件：`crates/arf-e2e/tests/tool_permission_deny.rs`（4 test cases）
- 驱动：E2EHarness + ScriptedProvider + 写 echo tool + 端到端跑
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test tool_permission_deny --manifest-path /home/wangxie/open_deepseek_arf/.worktrees/group6-extend/Cargo.toml -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.03s`**
- 关键运行输出：
  ```
  test deny_tool_runs_without_blocking ... [test1] Deny tool 端到端跑 OK (framework 不拦截) ✓
  test tool_permission_no_denied_msgtype ... [test2] framework 端无 tool_permission_denied msg_type ✓
  test tool_permission_deny_variant_traits ... [test3] arf_agent::ToolPermission::Deny 序列化为 "Deny" ✓
  test engine_runs_deny_tool_directly ... [test4] F-012 沿用: Deny 变体在 Engine 端死代码 ✓
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/tool_permission_deny.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：Deny tool 端到端跑

```
单元              : deny_tool_end_to_end × §2.5
能力等级           : F（FAIL — ToolPermission 路径未实现，tool 直接跑）
判定依据          : E2EHarness 写 1 个 tool.toml + ScriptedProvider 调 tool_call
                   + 期望 framework 拦截 → 实际 framework **直接跑** tool
                   端到端 work，ToolPermission 完全不拦截
file:line         : crates/arf-engine/src/engine.rs:534-598 do_tool_turn
                   直接发 tool_exec，无 permission 检查
                   ✗ ToolPermission::Deny 路径未实现
```

### 单元 2：framework 无 tool_permission_denied msg_type

```
单元              : no_denied_msgtype × §2.5
能力等级           : C（partial — framework 无 Deny 拦截信号）
判定依据          : grep framework 源码（arf-engine / arf-core / arf-bus / arf-agent/src）
                   无 `tool_permission_denied` 字符串出现
                   framework 端完全无 Deny 拒绝消息
file:line         : crates/arf-engine/src/engine.rs:534-598
                   ✗ 无 tool_permission_denied msg_type
```

### 单元 3：ToolPermission::Deny variant + serde

```
单元              : deny_variant_traits × §2.5
能力等级           : C（partial — enum 在 arf-agent 端存在，Engine 端不可达）
判定依据          : arf_agent::ToolPermission::Deny 序列化为 "Deny"（字符串）
                   arf_core::ToolSpec (Engine 用) 不含 permission 字段
                   两套 ToolSpec 不互通
file:line         : crates/arf-core/src/tool.rs:9-17 ToolSpec 结构 (Engine 用)
                   crates/arf-agent/src/tool.rs:10 ToolPermission enum
                   crates/arf-agent/src/tool.rs:16 Deny 变体
                   △ enum 在 arf-agent 端存在；Engine 不读
```

### 单元 4：Engine 端 Deny 工具照样执行

```
单元              : engine_runs_deny × §2.5
能力等级           : F（FAIL — Engine 端完全无 permission 字段）
判定依据          : arf_engine::AgentConfig fields: model, resources,
                   system_prompt_template, initial_memory, allowed_paths, engine
                   **无** tools: Vec<ToolSpec> 字段
                   Deny 工具与 Allow/Ask 工具行为完全一致
file:line         : crates/arf-engine/src/config.rs:77-94 AgentConfig
                   ✗ Engine 不接受 tool permission 声明
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `deny_tool_end_to_end` | **F** | tool 直接跑，Deny 路径未实现 |
| `no_denied_msgtype` | **C** | framework 无 `tool_permission_denied` msg_type |
| `deny_variant_traits` | **C** | enum 在 arf-agent 端存在，Engine 端不可达 |
| `engine_runs_deny` | **F** | Engine AgentConfig 无 tools 字段 |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（沿用 9.13.3 F-012 病灶）。

### 框架实际行为（按 spec §3.3 输出）

- `ToolPermission::Deny` 端到端跑：tool 直接执行 —— **F**
- framework 无 `tool_permission_denied` msg_type —— **C**
- `ToolPermission::Deny` enum 序列化 `"Deny"` 正常 —— **D**（trait 形态 OK）
- Engine AgentConfig 无 `tools: Vec<ToolSpec>` 字段 —— **F-012 沿用**

### 注意事项（潜在 issue，非 lesion）

1. **F-012 沿用**：Deny 与 Ask 行为完全一致，framework 端完全无 permission 路径。
   修复方向同 9.13.3：
   - 方案 A：arf_engine::AgentConfig 加 `tools: Vec<arf_agent::ToolSpec>` 字段
   - 方案 B：废弃 arf_agent::AgentConfig，统一 Engine 读 arf_agent::AgentConfig
   - 方案 C：Engine 接受 `Option<Arc<dyn ToolPermissionChecker>>` trait

2. **Deny 是"硬拒"，比 Ask 实现更简单**：Deny 不需要 round-trip（无需发问收答），
   只需 Engine 在 `do_tool_turn` 前 `match cfg.tools[i].permission` 即可。
   理论上 Deny 应先于 Ask 实现，但 framework 都没做——印证"两套 AgentConfig 不互通"
   是根本原因。

3. **production tool 风险**（Deny 缺失的更严重后果）：Deny 通常用于禁止
   dangerous tool（shell exec / file delete / network）。framework 无 Deny 拦截意味着：
   - 任何 tool 都可被 model 调用（无 framework-level block）
   - app 端必须靠 model 训练 / sandboxing 防御
   - 与 capability-matrix §1.1 L2 `tool_permission.Deny` 完全不一致

4. **Deny/Allow/Ask 三态在 framework 端均无实现**（与 F-010 / F-011 / F-012 同类）：
   declared but unwired 的扩展点模式在 ARF engine 端是 system-level 问题。

---

## §E 探查回归

- 9.13.1（node_offline_fail_session）4 test pass，未受本 task 影响
- 9.13.2（node_offline_switch_to）4 test pass，未受本 task 影响
- 9.13.3（tool_permission_ask）4 test pass，未受本 task 影响
- 9.13.4 新增 4 test pass
- 综合：9.13.4 = 4 test，**4 pass, 0 新 F-lesion（F-012 沿用）**
- F-001~F-012 关联本 task（9.13.4 沿用 F-012）

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 Deny tool 端到端 test | ✓ test1 pass (tool 直接跑，无拦截) |
| 1 个无 denied msg_type test | ✓ test2 pass (framework 端无 msg_type) |
| 1 个 Deny 变体 + serde test | ✓ test3 pass (Deny 是合法变体) |
| 1 个 Engine 跑 Deny 工具 test | ✓ test4 pass (F-012 沿用) |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion（F-012 沿用） |

> 结论：9.13.4 探查确认 `ToolPermission::Deny` 沿用 F-012：framework 完全无
> permission 路径。Deny 与 Ask 工具行为完全一致——都直接执行。Deny 是更简单的
> 实现（无 round-trip），但 framework 仍未实现，印证两套 AgentConfig 不互通是
> 根本原因。F-012 病灶已登记于 9.13.3 audit-probe §D，本 task 沿用。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/tool_permission_deny.rs`（~170 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.13.4.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（F-012 在 9.13.3 audit-probe §D 首次登记，本 task 沿用，未追加到 lesion-registry.md，遵循任务约束）
- 待 commit
