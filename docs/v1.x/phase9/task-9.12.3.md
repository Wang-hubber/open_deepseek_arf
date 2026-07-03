# 任务 9.12.3：自定义 Tool（含 / 不含 cancel）

> Phase 9 — 9.12 L 扩展点实现大类 · 第 3 task（依赖 9.5.x）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.x（tool integration）
> 输出物：`docs/v1.x/phase9/audit-probe-9.12.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.x 探查了 framework-supplied `ScriptTool`（subprocess）和 `HttpProxyTool`（HTTP）。本 task 探查 **app 端实现自定义 `Tool` trait**——capability matrix L8 列出的扩展点。

**Framework 现状**（待探查确认）：
- `Tool` trait（`crates/arf-mcp/src/tool.rs:11-41`）—— 4 必须方法（name / description / parameters_schema / execute）+ 1 default `cancel()` no-op
- `ScriptTool`（script.rs）—— script-based subprocess executor
- app 端实现 `Tool` trait 后可通过 `FsDiscovery` 的 in-memory 注入或 `ToolMap` 直接调

**关键探查问题**（不预设答案）：
1. app `impl Tool for MyTool` —— `execute()` 端到端 work？
2. 不 override `cancel()`（留 default no-op）—— 长跑 tool 怎么 cancel？framework 的 cancel 链路在哪？
3. override `cancel()`（设 atomic flag）—— cascade cancel 触发时 flag 真的被读？
4. 多个自定义 Tool 在同一 ToolCallSet 中混合使用——并行 / 串行 / 失败传递端到端 OK？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-mcp/src/tests/script_tests.rs`：ScriptTool 默认 execute 测试
- `crates/arf-e2e/tests/mcp_fs_discovery.rs`：9.5.1 端到端 probe（用 ScriptTool）
- `crates/arf-e2e/tests/custom_runtime_module.rs`：9.12.2 用 TestTool + CountingRuntime
- **本 task 不重复**：ScriptTool 端到端
- **本 task 聚焦**：app 自定义 `impl Tool for MyTool`（含 cancel / 不含 cancel）+ 端到端 execute + cancel 链路

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`custom_tool.rs`，4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `custom_tool_no_cancel_default` | `impl Tool for SimpleTool`：只 override 4 必须方法（name/desc/schema/execute），cancel 留 default no-op —— execute 端到端 work |
| 2 | `custom_tool_with_cancel` | `impl Tool for CancellableTool`：override cancel()（设 atomic flag）—— cancel() 被调 flag 被设 |
| 3 | `custom_tool_in_tool_map_execute` | 多个自定义 Tool 在 `HashMap<String, Arc<dyn Tool>>` 中 + `executor::execute()` —— 端到端 execute 端到端 work |
| 4 | `custom_tool_end_to_end_via_bus` | 自定义 Tool + FsDiscovery in-memory 注入 + McpNode + bus + tool_exec → 端到端 work |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub trait Tool\|fn execute\|fn cancel" crates/arf-mcp/src/tool.rs
grep -n "executor::execute\|pub async fn execute" crates/arf-mcp/src/executor.rs
```

逐行解释：
- `Tool::execute()` 抽象方法（tool.rs:27）
- `Tool::cancel()` 有 default impl no-op（tool.rs:38-40）
- `executor::execute()` DAG 调度（executor.rs:15+）
- `McpNode` 通过 `discovery.tool_map()` 取 tool 调 execute（node.rs:140, 194）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test custom_tool -- --nocapture --test-threads=1 2>&1 | tee /tmp/custom_tool_run.log
```

逐行解释：
- 4 test 应全过（自定义 Tool + execute + cancel + 端到端）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/custom_tool_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：Tool 是否一个职责（执行一个具体动作）？
- A2：Tool trait 与 RuntimeModule / DiscoveryBackend 正交？
- A3：tool name 跨 crate 唯一？
- A4：tool execution 集中？

**C. 输出**：`audit-probe-9.12.3.md`。
