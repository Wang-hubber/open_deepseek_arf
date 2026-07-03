# 任务 9.12.2：自定义 RuntimeModule（自定义 execute 策略）

> Phase 9 — 9.12 L 扩展点实现大类 · 第 2 task（依赖 9.5.6）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.6（McpNode + 自定义 RuntimeModule）
> 输出物：`docs/v1.x/phase9/audit-probe-9.12.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.6 探查了 `McpNode + 自定义 RuntimeModule` 概念。本 task 深入 **app 端实现自定义 `RuntimeModule` trait**——`execute()` 自定义策略。

**Framework 现状**（待探查确认）：
- `RuntimeModule` trait（`crates/arf-mcp/src/runtime.rs:19-50`）—— 3 方法（capabilities / execute / run_single）
- `execute()` 有 default impl 调 `executor::execute()`（DAG scheduling）
- `run_single()` 有 default impl 调 `tool.execute(params)`
- `LocalRuntime` / `RemoteRuntime` 框架内置（只 override capabilities）
- `McpNode::local_with_runtime(ns, root, runtime)`（node.rs:54-67）—— public 入口接收 `Box<dyn RuntimeModule>`

**关键探查问题**（不预设答案）：
1. app 实现 `MyRuntime` override `execute()` 完全不走 `executor::execute()` ——端到端 work？
2. override `run_single()` 单 tool 执行策略（pre/post 钩子 / 权限检查 / 日志）—— 端到端 work？
3. override 只 `capabilities()` 不动 execute —— 与 LocalRuntime 等价？
4. 自定义 Runtime 的 `execute()` 返回 `ToolResultSet` 但要构建自定义 status / error 格式——format 错误时 McpNode dispatch 会怎么处理？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-mcp/src/tests/runtime_tests.rs`：RuntimeModule 单元测试
- `crates/arf-mcp/src/tests/script_tests.rs`：ScriptTool 默认 Runtime 下的执行测试
- `crates/arf-e2e/tests/mcp_fs_discovery.rs`：9.5.1 端到端 probe（用 LocalRuntime）
- **本 task 不重复**：默认 Runtime 端到端
- **本 task 聚焦**：app 自定义 `impl RuntimeModule for MyRuntime`（override execute / run_single）+ 通过 `McpNode::local_with_runtime` 端到端

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`custom_runtime_module.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `custom_runtime_override_execute_strategy` | `impl RuntimeModule for CountingRuntime`：override `execute()`（不调 executor::execute，串行执行）—— tool_call_set 串行 end-to-end |
| 2 | `custom_runtime_override_run_single` | `impl RuntimeModule for LoggingRuntime`：override `run_single()`（pre/post 钩子 + 调 tool.execute）—— 单 tool 调用端到端 work |
| 3 | `custom_runtime_capabilities_only` | `impl RuntimeModule for CustomCapRuntime`：只 override `capabilities()`，execute / run_single 留默认 —— 与 LocalRuntime 等价 work |
| 4 | `custom_runtime_in_mcp_node_end_to_end` | 上面 CountingRuntime 通过 `McpNode::local_with_runtime` 注入 + bus + tool_exec → 端到端 work |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub trait RuntimeModule\|fn capabilities\|fn execute\|fn run_single" crates/arf-mcp/src/runtime.rs
grep -n "pub fn local_with_runtime" crates/arf-mcp/src/node.rs
```

逐行解释：
- `RuntimeModule::capabilities()` 必须 override
- `execute()` 有 default impl 调 executor
- `run_single()` 有 default impl 调 tool.execute
- `McpNode::local_with_runtime(ns, root, runtime)` 接受 `Box<dyn RuntimeModule>`

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test custom_runtime_module -- --nocapture --test-threads=1 2>&1 | tee /tmp/custom_runtime_module_run.log
```

逐行解释：
- 4 test 应全过（自定义 Runtime + 注入 McpNode + 端到端 execute）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/custom_runtime_module_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：RuntimeModule 是否一个职责（执行策略）？execute / run_single / capabilities 分工？
- A2：RuntimeModule 与 DiscoveryBackend / Tool 各自 trait 边界正交？
- A3：capabilities JSON 跨 crate 唯一？
- A4：tool execution 集中？

**C. 输出**：`audit-probe-9.12.2.md`。
